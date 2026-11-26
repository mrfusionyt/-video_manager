# FileTransferServer 1.0
Windows 11 Python file upload web app; LAN/Tailscale use.

## Setup
1. Install Python 3.10+ (recommended 3.12) and VS Code.
2. Extract ZIP, run `install.bat`.
3. Edit `config.json`, change the default password.
4. Run `start.bat`.

## Connect
- Local PC: http://127.0.0.1:8765
- LAN: http://WINDOWS-LAN-IP:8765
- Tailscale: http://WINDOWS-TAILSCALE-IP:8765

Login is HTTP Basic authentication. Default username: `transfer`.
Change the password before use. Never port-forward this server to public internet.
Folder picker relies on browser support (`webkitdirectory`); Android may not offer it, but regular file picker works.
Port setting in web page is stored; to apply, also edit the port number in `start.bat`, then restart.
This MVP sends a selected batch in one request. For interrupted transfers, retry; for very large file collections, upload smaller batches.
