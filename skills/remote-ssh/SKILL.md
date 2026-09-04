---
name: remote-ssh
description: |
  连接 Dr. Pie 中配置的远程服务器，执行 SSH 远程命令、查看远程文件/目录、检查服务器环境。
  Use when the user asks to connect/login to a remote server, inspect remote files,
  run an ad-hoc SSH command, or when another skill needs the canonical Dr. Pie
  remote-server command pattern. Handles the macOS/Linux drpie-ssh wrapper and
  Windows PowerShell plink separately.
author: Dr. Pie
version: 1.0.0
date: 2026-07-07
---

# Remote SSH Helper

Use this skill whenever you need to connect to the active remote server configured in Dr. Pie.

## Inputs From Dr. Pie

The desktop injects the active server into environment variables:

| Variable | Meaning |
| --- | --- |
| `BIGPIE_REMOTE_HOST` | SSH host / IP |
| `BIGPIE_REMOTE_USER` | SSH user |
| `BIGPIE_REMOTE_PORT` | SSH port; may be empty, then default to 22 |
| `BIGPIE_REMOTE_AUTH` | `password` or `key` |
| `SSHPASS` / `BIGPIE_REMOTE_PASSWORD` | Password when password auth is configured |

Before running any remote command, verify the required variables exist. Do not ask the user for host/user/password if these env vars are present.

## Choose By Local Platform

The command pattern depends on the machine running Dr. Pie, not the remote server.

| Local platform | Use |
| --- | --- |
| macOS / Linux shell | `drpie-ssh ...` |
| Windows PowerShell | `plink.exe` directly |

Do not use the Windows `SSH_ASKPASS` path. It is unreliable for OpenSSH on Windows.

`drpie-ssh` is bundled by Dr. Pie. In password mode it uses `sshpass -e` while
disabling public-key/SSH-agent probing, so short-lived commands do not repeatedly
touch another app's protected SSH-agent socket and trigger macOS privacy dialogs.
In key mode it delegates to normal `ssh` and preserves the configured SSH agent.
Do not bypass it with raw `sshpass -e ssh`.

## macOS / Linux

For one-shot commands:

```bash
drpie-ssh -o StrictHostKeyChecking=no${BIGPIE_REMOTE_PORT:+ -p $BIGPIE_REMOTE_PORT} \
  "$BIGPIE_REMOTE_USER@$BIGPIE_REMOTE_HOST" \
  "bash -l -c '<COMMAND>'"
```

For interactive commands that need a TTY, such as `hope login`:

```bash
drpie-ssh -t -o StrictHostKeyChecking=no${BIGPIE_REMOTE_PORT:+ -p $BIGPIE_REMOTE_PORT} \
  "$BIGPIE_REMOTE_USER@$BIGPIE_REMOTE_HOST" \
  "bash -l -c '<COMMAND>'"
```

Rules:

- Use `bash -l -c` for remote commands that depend on login-shell PATH.
- Use `drpie-ssh`; never place the password in the command line with `-p '<PASSWORD>'`.
- Use `-t` only when the remote command truly needs a TTY.

## Windows PowerShell

Prefer direct `plink.exe`, not `sshpass.cmd`, for commands with quoting, shell metacharacters, or first-connect host key handling.

Resolve the bundled plink first from PATH, then from the packaged Dr. Pie resource path:

```powershell
$plink = (Get-Command plink.exe -ErrorAction SilentlyContinue).Source
if (-not $plink) {
  $plink = Join-Path $env:LOCALAPPDATA 'Programs\Dr. Pie\resources\bundled-cli\plink.exe'
}
if (-not (Test-Path $plink)) { throw 'plink.exe not found in Dr. Pie bundled-cli' }
$portArgs = @()
if ($env:BIGPIE_REMOTE_PORT) { $portArgs = @('-P', $env:BIGPIE_REMOTE_PORT) }
```

Use a known host key fingerprint when available:

```powershell
$remote = "bash -l -c '<COMMAND>'"
& $plink -ssh -batch `
  -hostkey '<SHA256:FINGERPRINT_FROM_PLINK>' `
  -pw $env:SSHPASS `
  @portArgs `
  "$env:BIGPIE_REMOTE_USER@$env:BIGPIE_REMOTE_HOST" `
  $remote
```

If the host key is not known yet, run once without `-hostkey`, capture plink stderr, extract the `SHA256:` fingerprint, then retry with `-hostkey`.

```powershell
$remote = "bash -l -c '<COMMAND>'"
$errFile = New-TemporaryFile
& $plink -ssh -batch `
  -pw $env:SSHPASS `
  @portArgs `
  "$env:BIGPIE_REMOTE_USER@$env:BIGPIE_REMOTE_HOST" `
  $remote 2> $errFile
$code = $LASTEXITCODE
if ($code -ne 0) {
  $err = Get-Content $errFile -Raw
  $m = [regex]::Match($err, 'SHA256:[A-Za-z0-9+/]{43}')
  if ($err -match 'host key is not cached' -and $m.Success) {
    & $plink -ssh -batch `
      -hostkey $m.Value `
      -pw $env:SSHPASS `
      @portArgs `
      "$env:BIGPIE_REMOTE_USER@$env:BIGPIE_REMOTE_HOST" `
      $remote
  } else {
    throw $err
  }
}
Remove-Item $errFile -Force -ErrorAction SilentlyContinue
```

For multi-line remote scripts, pipe the script to remote `bash -l -s`:

```powershell
$script = @'
set -e
hostname
pwd
'@
$script | & $plink -ssh -batch `
  -hostkey '<SHA256:FINGERPRINT_FROM_PLINK>' `
  -pw $env:SSHPASS `
  @portArgs `
  "$env:BIGPIE_REMOTE_USER@$env:BIGPIE_REMOTE_HOST" `
  "bash -l -s"
```

Rules:

- Use `$env:SSHPASS`, `$env:BIGPIE_REMOTE_HOST`, `$env:BIGPIE_REMOTE_USER`, `$env:BIGPIE_REMOTE_PORT`.
- Do not hardcode a user's password.
- Do not hardcode one team's host key in this general skill; use a provided fingerprint or extract it from plink output.
- If `BIGPIE_REMOTE_PORT` is empty, omit `-P`.

## Quick Connectivity Test

macOS / Linux:

```bash
drpie-ssh -o StrictHostKeyChecking=no${BIGPIE_REMOTE_PORT:+ -p $BIGPIE_REMOTE_PORT} \
  "$BIGPIE_REMOTE_USER@$BIGPIE_REMOTE_HOST" "echo OK"
```

Windows PowerShell:

```powershell
$portArgs = @()
if ($env:BIGPIE_REMOTE_PORT) { $portArgs = @('-P', $env:BIGPIE_REMOTE_PORT) }
$remote = 'echo OK'
& $plink -ssh -batch -pw $env:SSHPASS @portArgs "$env:BIGPIE_REMOTE_USER@$env:BIGPIE_REMOTE_HOST" $remote
```

Expected output: `OK`.

## Failure Handling

| Symptom | Likely cause | Action |
| --- | --- | --- |
| `SSHPASS is not set` / empty password env | Active server has no saved password | Ask the user to save/test the server in Dr. Pie settings |
| `host key is not cached` on Windows | plink first-connect trust prompt cannot run in `-batch` | Extract `SHA256:` from stderr and retry with `-hostkey` |
| `Connection refused` | Wrong host/port or CodeLab forwarding not started | Check `BIGPIE_REMOTE_HOST` / `BIGPIE_REMOTE_PORT`; for CodeLab, confirm port forwarding |
| `Permission denied` | Wrong password or user | Ask user to re-save password or verify account |
| Command works on macOS but fails in Windows PowerShell | Quoting passed through `sshpass.cmd` | Use direct `plink.exe` pattern above |
