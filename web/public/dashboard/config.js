// Public configuration for the dashboard. None of these are secrets: the app
// client is a public PKCE client with no secret, by design.
window.HEARALLY_CONFIG = Object.freeze({
  api: "https://api.hearally.com",
  authOrigin: "https://hearally-prod-499817841945.auth.us-east-1.amazoncognito.com",
  clientId: "3n0n0guik8ena7qstisv5g6800",
  redirectUri: "https://hearally.com/dashboard/callback/",
  logoutUri: "https://hearally.com/dashboard/signed-out/",
});
