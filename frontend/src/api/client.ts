import axios from 'axios'

// Relative '/api' works in dev (Vite proxy) and prod (nginx proxy).
const client = axios.create({
  baseURL: import.meta.env.VITE_API_BASE ?? '/api',
})

client.interceptors.request.use((config) => {
  const token = localStorage.getItem('access')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

export default client
