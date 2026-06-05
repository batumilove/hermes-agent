# Sandbox manager terminal backend

Hermes can route terminal/tooling jobs through the external Agent Sandbox Manager by selecting the `sandbox_manager` terminal backend. The backend is SSH/CLI-first and private-network only; it sends a JSON job spec to the manager and does not mount Hermes profile directories, Infisical identities, Tailscale auth keys, SSH agents/keys, backup mounts, or broad host secrets into the job.

Example for the current MVP sandbox host:

```bash
hermes config set terminal.backend sandbox_manager
hermes config set terminal.sandbox_ssh_host 192.168.10.141
hermes config set terminal.sandbox_ssh_user ubuntu
hermes config set terminal.sandbox_ssh_key /home/ubuntu/hermes-workspace/sandbox_vm_key
hermes config set terminal.sandbox_manager_dir /home/ubuntu/agent-sandbox-manager
hermes config set terminal.sandbox_config config/sandbox-manager.test.json
hermes config set terminal.sandbox_runtime alpine
hermes config set terminal.sandbox_network offline
hermes config set terminal.timeout 10
```

Settings:

- `terminal.sandbox_runtime`: manager runtime alias, e.g. `alpine`, `python`, `node`. Empty uses the manager default.
- `terminal.sandbox_network`: defaults to `offline`. Use `internet` only for explicit egress opt-in when the manager config allows it.
- `terminal.sandbox_output_bytes`: output cap requested from the manager/client path; Hermes still applies its normal tool-output cap before returning to the model.
- `terminal.sandbox_env`: explicit per-job environment map. The client rejects likely secret-bearing names and prefixes (`HERMES*`, `INFISICAL*`, `TAILSCALE*`, `TS_*`, `SSH_*`, common API-key names).
- `terminal.sandbox_trusted`: default `false`; do not enable for untrusted code.

Limitations:

- `execute_code` is intentionally not supported with `terminal.backend=sandbox_manager` yet. The current manager runs one-shot isolated jobs and does not provide the persistent remote filesystem needed by `execute_code`'s file-based RPC transport. Use `terminal()` for this MVP backend.
- `stdin_data` is rejected by the client backend instead of being silently dropped or misrouted; pass non-secret input through the command or a reviewed file path.

Returned terminal results preserve `sandbox_result` audit metadata from the manager: image ref/digest, network profile, duration, exit status, timeout/resource status, cleanup result, and truncation flags.

Operational notes:

- Keep the endpoint Tailscale/private-LAN only. Do not add public routes or Cloudflare tunnels for this backend.
- The MVP manager currently validates the runsc compatibility lane. Treat arbitrary hostile-code execution as not production-ready until the Kata/containerd backend and Tailscale enrollment are completed.
- For one-off internet access, set `terminal.sandbox_network internet`, run the job, then switch back to `offline`.
