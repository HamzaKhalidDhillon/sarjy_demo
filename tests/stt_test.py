"""Simple test script to POST an audio file to the /stt endpoint.

Usage:
  python tests/stt_test.py /path/to/audio.webm
"""
import sys
import httpx

def main():
    if len(sys.argv) < 2:
        print("Usage: python tests/stt_test.py /path/to/audio.webm")
        sys.exit(1)
    path = sys.argv[1]
    files = {"audio": open(path, "rb")}
    data = {"conversation_id": ""}
    with httpx.Client(timeout=60.0) as c:
        login = c.post("http://localhost:8000/login", json={"username": "test-user", "password": "test-pass"})
        headers = {"Authorization": f"Bearer {login.json()['token']}"}
        r = c.post("http://localhost:8000/stt", data=data, files=files, headers=headers)
        print(r.status_code)
        print(r.text)

if __name__ == '__main__':
    main()
