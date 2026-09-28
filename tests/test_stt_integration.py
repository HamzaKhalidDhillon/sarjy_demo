import os
import subprocess
import time
import httpx


def test_stt_endpoint_local_file(tmp_path):
    # Requires backend running at localhost:8000
    sample = tmp_path / "sample.webm"
    # Create a tiny silent webm file using ffmpeg if available, otherwise skip
    ffmpeg = subprocess.run(["which", "ffmpeg"], capture_output=True)
    if ffmpeg.returncode != 0:
        print("ffmpeg not available; skipping integration test")
        return

    cmd = [
        "ffmpeg", "-f", "lavfi", "-i", "anullsrc=channel_layout=mono:sample_rate=16000", "-t", "0.5",
        str(sample)
    ]
    subprocess.run(cmd, check=True)

    files = {"audio": open(sample, "rb")}
    with httpx.Client(timeout=60.0) as c:
        login = c.post("http://localhost:8000/login", json={"username": "test-user", "password": "test-pass"})
        headers = {"Authorization": f"Bearer {login.json()['token']}"}
        r = c.post("http://localhost:8000/transcribe", files=files, headers=headers)
        assert r.status_code == 200
        j = r.json()
        assert "transcript" in j
