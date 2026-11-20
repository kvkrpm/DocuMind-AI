import { useState, useEffect } from 'react'
import axios from 'axios'

const API = import.meta.env.VITE_API_URL || ''
const api = axios.create({ baseURL: API })
const initialParams = new URLSearchParams(window.location.hash.slice(1))
const initialResetToken = initialParams.get('reset_token')
const initialVerifyToken = initialParams.get('verify_token')

api.interceptors.request.use((config) => {
  const token = sessionStorage.getItem('access_token') || sessionStorage.getItem('guest_session_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

export default function App() {
  const [docs, setDocs] = useState([])
  const [question, setQuestion] = useState('')
  const [chat, setChat] = useState([])
  const [loading, setLoading] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState('')
  const [token, setToken] = useState(
    () => sessionStorage.getItem('access_token') || sessionStorage.getItem('guest_session_token'),
  )
  const [isGuest, setIsGuest] = useState(
    () => !sessionStorage.getItem('access_token') && Boolean(sessionStorage.getItem('guest_session_token')),
  )
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [authMode, setAuthMode] = useState(
    initialResetToken ? 'reset' : initialVerifyToken ? 'verifying' : 'login',
  )
  const [actionToken, setActionToken] = useState(initialResetToken || initialVerifyToken || '')
  const [authError, setAuthError] = useState('')
  const [authNotice, setAuthNotice] = useState('')
  const [authLoading, setAuthLoading] = useState(false)

  const fetchDocs = async () => {
    const res = await api.get('/api/documents')
    setDocs(res.data)
  }

  const logOut = () => {
    sessionStorage.removeItem('access_token')
    sessionStorage.removeItem('guest_session_token')
    setToken(null)
    setIsGuest(false)
    setDocs([])
    setChat([])
  }

  const endGuestSession = async () => {
    try {
      await api.delete('/api/guest/session')
    } catch (err) {
      if (err.response?.status !== 401) {
        setAuthError(err.response?.data?.detail || err.message)
      }
    } finally {
      logOut()
    }
  }

  const startGuestSession = async () => {
    setAuthLoading(true)
    setAuthError('')
    setAuthNotice('')
    try {
      const res = await api.post('/api/guest/session')
      sessionStorage.removeItem('access_token')
      sessionStorage.setItem('guest_session_token', res.data.access_token)
      setIsGuest(true)
      setToken(res.data.access_token)
    } catch (err) {
      setAuthError(err.response?.data?.detail || err.message)
    } finally {
      setAuthLoading(false)
    }
  }

  useEffect(() => {
    if (!token) return
    fetchDocs().catch((err) => {
      if (err.response?.status === 401) {
        logOut()
        setAuthError('Your session expired. Start a new guest session or sign in again.')
      } else {
        setAuthError(err.response?.data?.detail || err.message)
      }
    })
  }, [token])

  useEffect(() => {
    if (!initialVerifyToken) return
    api.post('/api/auth/verify-email', { token: initialVerifyToken })
      .then((res) => {
        sessionStorage.setItem('access_token', res.data.access_token)
        setToken(res.data.access_token)
        window.history.replaceState({}, '', window.location.pathname)
      })
      .catch((err) => {
        setAuthMode('login')
        setAuthError(err.response?.data?.detail || 'The verification link is invalid or expired.')
        window.history.replaceState({}, '', window.location.pathname)
      })
  }, [])

  const handleAuth = async (event) => {
    event.preventDefault()
    setAuthLoading(true)
    setAuthError('')
    setAuthNotice('')
    try {
      if (authMode === 'forgot') {
        const res = await api.post('/api/auth/password/forgot', { email })
        setAuthNotice(res.data.message)
      } else if (authMode === 'reset') {
        const res = await api.post('/api/auth/password/reset', {
          token: actionToken,
          password,
        })
        window.history.replaceState({}, '', window.location.pathname)
        setAuthMode('login')
        setAuthNotice(res.data.message)
        setPassword('')
      } else {
        const endpoint = authMode === 'register' ? '/api/auth/register' : '/api/auth/login'
        const res = await api.post(endpoint, { email, password })
        if (authMode === 'register') {
          setEmail(res.data.user.email)
          setAuthMode('verify_pending')
          setAuthNotice(res.data.message)
        } else {
          sessionStorage.removeItem('guest_session_token')
          sessionStorage.setItem('access_token', res.data.access_token)
          setIsGuest(false)
          setToken(res.data.access_token)
          setPassword('')
        }
      }
    } catch (err) {
      if (authMode === 'login' && err.response?.status === 403) {
        setAuthMode('verify_pending')
        setAuthNotice(err.response.data.detail)
        setEmail(email.trim())
      } else {
        setAuthError(err.response?.data?.detail || err.message)
      }
    } finally {
      setAuthLoading(false)
    }
  }

  const resendVerification = async () => {
    setAuthLoading(true)
    setAuthError('')
    setAuthNotice('')
    try {
      const res = await api.post('/api/auth/verify-email/resend', { email })
      setAuthNotice(res.data.message)
    } catch (err) {
      setAuthError(err.response?.data?.detail || err.message)
    } finally {
      setAuthLoading(false)
    }
  }

  const handleUpload = async (event) => {
    const file = event.target.files[0]
    if (!file) return
    setUploading(true)
    setUploadError('')
    const formData = new FormData()
    formData.append('file', file)
    try {
      await api.post('/api/documents/upload', formData)
      await fetchDocs()
    } catch (err) {
      setUploadError(
        err.response?.data?.detail ||
        (err.message === 'Network Error'
          ? 'Could not reach the server. Check your connection and try again.'
          : err.message),
      )
    } finally {
      setUploading(false)
      event.target.value = ''
    }
  }

  const handleDelete = async (id) => {
    try {
      await api.delete(`/api/documents/${id}`)
      await fetchDocs()
    } catch (err) {
      alert(err.response?.data?.detail || err.message)
    }
  }

  const ask = async () => {
    if (!question.trim()) return
    if (docs.length === 0) {
      alert('Upload PDFs first')
      return
    }
    setLoading(true)
    const currentQuestion = question
    setQuestion('')
    setChat((previous) => [...previous, { role: 'user', text: currentQuestion }])
    try {
      const res = await api.post('/api/query', {
        question: currentQuestion,
        doc_ids: docs.map((doc) => doc.id),
      })
      setChat((previous) => [
        ...previous,
        {
          role: 'assistant',
          text: res.data.answer,
          sources: res.data.sources,
          hasContext: res.data.has_sufficient_context,
        },
      ])
    } catch (err) {
      setChat((previous) => [
        ...previous,
        { role: 'assistant', text: `Error: ${err.response?.data?.detail || err.message}` },
      ])
    } finally {
      setLoading(false)
    }
  }

  if (!token) {
    if (authMode === 'verifying') {
      return (
        <main className="min-h-screen bg-zinc-50 flex items-center justify-center p-4">
          <div className="w-full max-w-sm bg-white border rounded-2xl p-6 text-center">
            <h1 className="text-xl font-bold">Verifying your email...</h1>
          </div>
        </main>
      )
    }

    return (
      <main className="min-h-screen bg-zinc-50 flex items-center justify-center p-4">
        <form onSubmit={handleAuth} className="w-full max-w-sm bg-white border rounded-2xl p-6">
          <h1 className="text-xl font-bold">📄 DocuMind AI</h1>
          <p className="text-sm text-zinc-500 mt-1">
            {authMode === 'register'
              ? 'Create an account to keep your documents private.'
              : authMode === 'forgot'
                ? 'Enter your email and we will send a reset link if an account exists.'
                : authMode === 'reset'
                  ? 'Choose a new password for your account.'
                  : authMode === 'verify_pending'
                    ? 'Verify your email address to finish creating your account.'
                    : 'Sign in to your account.'}
          </p>
          {authMode === 'verify_pending' ? (
            <div className="mt-5">
              <p className="text-sm text-zinc-600">Email: {email}</p>
              {authNotice && <p role="status" className="text-sm text-emerald-700 mt-3">{authNotice}</p>}
              {authError && <p role="alert" className="text-sm text-red-600 mt-3">{authError}</p>}
              <button
                type="button"
                disabled={authLoading}
                onClick={resendVerification}
                className="w-full bg-black text-white rounded-lg py-2 mt-5 disabled:opacity-50"
              >
                {authLoading ? 'Please wait...' : 'Resend verification email'}
              </button>
              <button type="button" onClick={() => { setAuthMode('login'); setAuthNotice(''); setAuthError('') }} className="w-full text-sm text-zinc-600 mt-4">
                Back to sign in
              </button>
            </div>
          ) : (
          <>
          {authMode !== 'reset' && (
          <label className="block text-sm mt-5">
            Email
            <input
              type="email"
              autoComplete="email"
              required
              maxLength={254}
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className="block w-full border rounded-lg px-3 py-2 mt-1"
            />
          </label>
          )}
          {(authMode === 'register' || authMode === 'login' || authMode === 'reset') && (
          <label className="block text-sm mt-4">
            Password
            <input
              type="password"
              autoComplete={authMode === 'login' ? 'current-password' : 'new-password'}
              required
              minLength={12}
              maxLength={128}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="block w-full border rounded-lg px-3 py-2 mt-1"
            />
            {authMode === 'register' && (
              <span className="text-xs text-zinc-500">Use at least 12 characters.</span>
            )}
          </label>
          )}
          {authNotice && <p role="status" className="text-sm text-emerald-700 mt-3">{authNotice}</p>}
          {authError && <p role="alert" className="text-sm text-red-600 mt-3">{authError}</p>}
          <button
            type="submit"
            disabled={authLoading}
            className="w-full bg-black text-white rounded-lg py-2 mt-5 disabled:opacity-50"
          >
            {authLoading
              ? 'Please wait...'
              : authMode === 'register'
                ? 'Create account'
                : authMode === 'forgot'
                  ? 'Send reset link'
                  : authMode === 'reset'
                    ? 'Reset password'
                    : 'Sign in'}
          </button>
          {authMode === 'login' && (
            <button type="button" onClick={() => { setAuthMode('forgot'); setAuthError(''); setAuthNotice('') }} className="w-full text-sm text-zinc-600 mt-4">
              Forgot password?
            </button>
          )}
          {authMode !== 'reset' && (
            <button
              type="button"
              onClick={() => {
                setAuthMode(authMode === 'register' ? 'login' : 'register')
                setAuthError('')
                setAuthNotice('')
              }}
              className="w-full text-sm text-zinc-600 mt-4"
            >
              {authMode === 'register' ? 'Already have an account? Sign in' : 'New here? Create an account'}
            </button>
          )}
          {authMode === 'login' && (
            <>
              <button
                type="button"
                disabled={authLoading}
                onClick={startGuestSession}
                className="w-full border rounded-lg py-2 mt-4 disabled:opacity-50"
              >
                {authLoading ? 'Please wait...' : 'Continue as guest'}
              </button>
              <p className="text-xs text-zinc-500 mt-2">
                Guest access ends when this tab closes; workspace data expires automatically. Document text is sent to Gemini. Avoid sensitive files.
              </p>
            </>
          )}
          {(authMode === 'forgot' || authMode === 'reset') && (
            <button type="button" onClick={() => { setAuthMode('login'); setAuthError(''); setAuthNotice('') }} className="w-full text-sm text-zinc-600 mt-4">
              Back to sign in
            </button>
          )}
          </>
          )}
        </form>
      </main>
    )
  }

  return (
    <div className="min-h-screen bg-zinc-50 text-zinc-900 flex flex-col md:flex-row md:h-screen">
      <div className="w-full md:w-[360px] bg-white border-b md:border-b-0 md:border-r p-4 md:p-5 flex flex-col md:shrink-0 md:min-h-0">
        <div className="flex items-start justify-between gap-2">
          <div>
            <h1 className="text-xl font-bold mb-1">📄 DocuMind AI</h1>
            <p className="text-xs text-zinc-500 mb-4">
              {isGuest
                ? 'Temporary guest workspace; expires automatically. Access ends when this tab closes.'
                : 'Upload up to 30 PDFs (10MB each)'}
            </p>
          </div>
          <button
            onClick={isGuest ? endGuestSession : logOut}
            className="shrink-0 whitespace-nowrap text-xs text-zinc-600 underline"
          >
            {isGuest ? 'End guest session' : 'Sign out'}
          </button>
        </div>

        {authError && <p role="alert" className="text-xs text-red-600 mb-2">{authError}</p>}
        {uploadError && <p role="alert" className="text-xs text-red-600 mb-2">{uploadError}</p>}
        <label className="border-2 border-dashed rounded-xl p-4 md:p-6 text-center cursor-pointer hover:bg-zinc-50 mb-4">
          <input type="file" accept=".pdf" hidden onChange={handleUpload} />
          <div className="text-sm font-medium">{uploading ? 'Uploading...' : 'Click to upload PDF'}</div>
        </label>

        <div className="flex-1 min-h-0 max-h-48 md:max-h-none overflow-y-auto space-y-2">
          {docs.map((doc) => (
            <div key={doc.id} className="border rounded-lg p-3 text-sm">
              <div className="font-medium truncate">{doc.name}</div>
              <div className="text-xs text-zinc-500">{doc.pages} pages • {doc.chunks} chunks • {doc.size_mb}MB</div>
              <button onClick={() => handleDelete(doc.id)} className="text-xs text-red-500 mt-1">Delete</button>
            </div>
          ))}
          {docs.length === 0 && <div className="text-xs text-zinc-400 text-center mt-10">No documents yet</div>}
        </div>
        <div className="text-[11px] text-zinc-400 mt-4">FastAPI • LangChain • ChromaDB • Gemini</div>
      </div>

      <div className="flex-1 flex flex-col min-h-[65vh] md:min-h-0">
        <div className="flex-1 min-h-0 overflow-y-auto p-4 md:p-6 space-y-4">
          {chat.length === 0 && (
            <div className="max-w-2xl mx-auto mt-20 text-center">
              <h2 className="text-2xl font-semibold">Ask anything about your documents</h2>
              <p className="text-zinc-500 mt-2">Answers include source citations with document name and page number.</p>
              <div className="grid grid-cols-2 gap-2 mt-6 text-sm">
                <button onClick={() => setQuestion('What is the main topic of the documents?')} className="border rounded p-2 text-left">What is the main topic?</button>
                <button onClick={() => setQuestion('Summarize the key findings with page references')} className="border rounded p-2 text-left">Summarize key findings</button>
                <button onClick={() => setQuestion('What does the document say about...?')} className="border rounded p-2 text-left">Search specific clause</button>
                <button onClick={() => setQuestion('List all dates mentioned')} className="border rounded p-2 text-left">List all dates mentioned</button>
              </div>
            </div>
          )}
          {chat.map((message, index) => (
            <div key={index} className={`max-w-3xl ${message.role === 'user' ? 'ml-auto bg-black text-white' : 'bg-white border'} rounded-2xl p-4`}>
              <div className="whitespace-pre-wrap break-words text-sm leading-relaxed">{message.text}</div>
              {message.sources?.length > 0 && (
                <div className="mt-3 border-t pt-3">
                  <div className="text-xs font-semibold mb-2">Sources ({message.sources.length})</div>
                  <div className="space-y-2">
                    {message.sources.map((source, index) => (
                      <div key={index} className="bg-zinc-50 rounded-lg p-2 text-xs">
                        <div className="font-medium">📄 {source.document_name} — Page {source.page_number} (score {source.score.toFixed(2)})</div>
                        <div className="text-zinc-600 mt-1 line-clamp-3">{source.chunk_text}...</div>
                      </div>
                    ))}
                  </div>
                  {!message.hasContext && <div className="text-xs text-amber-600 mt-2">⚠️ Documents do not contain enough information</div>}
                </div>
              )}
            </div>
          ))}
          {loading && <div className="text-sm text-zinc-400">Thinking...</div>}
        </div>

        <div className="border-t bg-white p-3 md:p-4">
          <div className="max-w-3xl mx-auto flex w-full gap-2">
            <input
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={(event) => event.key === 'Enter' && ask()}
              placeholder="Ask a question about your PDFs..."
              className="flex-1 min-w-0 border rounded-full px-4 py-3 text-sm outline-none"
            />
            <button onClick={ask} disabled={loading} className="shrink-0 bg-black text-white rounded-full px-4 md:px-6 text-sm font-medium">Ask</button>
          </div>
        </div>
      </div>
    </div>
  )
}
