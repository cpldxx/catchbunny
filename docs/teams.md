# Serve it for a team

```bash
catchbunny start --team --host 0.0.0.0 --public-url https://qa.example.com
```

The same server, shared. `--public-url` is the address people and agents will type; put a reverse proxy with a certificate in front (with Caddy: `qa.example.com { reverse_proxy 127.0.0.1:8700 }`).

- **Getting in.** The first start prints a link with a setup code; whoever opens it becomes the owner. The owner sees the **team code**; a colleague creates their own account with it at `/app/join`. The owner can replace the code (the old one stops working, people stay) and remove a person (their sign-ins, keys and connectors stop at once).
- **Keys.** A person makes a key per place they use it (an agent, a CI job) on the Connect an agent page. It is shown once. It works as `Authorization: Bearer cb_...` on `/v1` and on `/mcp`.
- **Connectors.** A client that cannot hold a key signs the person in with OAuth 2.1 (authorization code + PKCE, dynamic client registration), using the MCP SDK's authorization-server routes with Catchbunny as the provider (`oauth.py`). This needs HTTPS; on plain HTTP other than on the machine itself, the server offers keys only.
- **What is kept.** Passwords are stretched with scrypt. Sessions, keys, the setup code's use and connector tokens are stored as hashes. The cookie is HttpOnly and SameSite (Secure on HTTPS); a state-changing request from another origin is refused even with it; eight wrong passwords for one address in five minutes make that address wait.
- **On your own machine nothing changes**: no accounts. But `catchbunny start` answers only requests whose `Host` is localhost and sends no cross-origin headers, so a web page you happen to have open cannot drive it; and it refuses to listen on the network unless it is serving a team.

One team per server, shared apps and runs, email and password only (no single sign-on, no password reset by email).
