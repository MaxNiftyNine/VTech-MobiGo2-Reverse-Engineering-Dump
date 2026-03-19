# MobiGo Notes

This is some stuff I made with Codex while messing with the VTech MobiGo 2. It might work with the MobiGo 1 too, but I do not have one to test with.

## WARNINGS

- Everything in this project was made by Codex, so treat any script or command not listed in this README as non-functional.
- If you try unlisted commands, the output may be bad.
- Almost all of the stuff in the misc folder is incomplete or wrong.
- The official Learning Lodge program may automatically try to recover corrupted files. Replacing some files can brick a MobiGo.
- You may want to end `AgentMonitor` and `DownloadManager` in Task Manager before doing MobiGo USB work if Learning Lodge is installed.
- This repo is Windows-only because it depends on DLLs from the real VTech software. (in the vendor folder of the repo.)

## 1. Writing Files To MobiGo 2

Example write command that replaces Hamster Highway:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\write_mobigo_one.ps1" -RemotePath "A:\BUNDLE\G1\135800G1.MBA" -InputPath "C:\path\to\example.MBA"
```

## 2. Downloading Files From VTech Servers

You need a valid token. Get it by looking at HTTPS traffic while using Learning Lodge. `mitmproxy` works for this.

```powershell
python .\download_mobigo_system_files.py `
  --pid 11584 `
  --country US `
  --lang eng `
  --token YOUR_TOKEN_HERE `
  --out-dir .\mobigo_system_files `
  --insecure
```

There should also be some existing dumps in the misc/mobigo_system_files_11584 folder.
