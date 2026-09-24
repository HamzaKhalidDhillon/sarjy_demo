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
    data = {"user_id": "test-user", "conversation_id": ""}
    with httpx.Client(timeout=60.0) as c:
        r = c.post("http://localhost:8000/stt", data=data, files=files)
        print(r.status_code)
        print(r.text)

if __name__ == '__main__':
    main()
