/**
 * Keep local development on one browser origin so host-only auth cookies are
 * not split between localhost and 127.0.0.1.
 */
export function canonicalLocalOrigin(requestUrl, requestHost, port = 5176) {
  const hostname = String(requestHost || '').split(':')[0].toLowerCase()
  if (hostname !== 'localhost') return null

  const url = new URL(requestUrl || '/', `http://${requestHost}`)
  url.protocol = 'http:'
  url.hostname = '127.0.0.1'
  url.port = url.port || String(port)
  return url.toString()
}
