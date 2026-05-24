import requests
import json
import os
import uuid
from dotenv import load_dotenv

load_dotenv()

API_BASE_URL = "https://api.ecoledirecte.com/v3"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

VERSIONS_TO_TEST = ["4.75.0", "4.96.3", "4.100.0", "4.104.2", "4.108.0"]

def test_versions():
    username = os.getenv("ED_USERNAME")
    password = os.getenv("ED_PASSWORD")
    device_uuid = str(uuid.uuid4())
    
    for version in VERSIONS_TO_TEST:
        print(f"\n=============================================")
        print(f"TESTING API_VERSION: {version}")
        print(f"=============================================")
        
        session = requests.Session()
        session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/x-www-form-urlencoded",
        })
        
        # 1. GET GTK
        session.get(f"{API_BASE_URL}/login.awp?gtk=1&v={version}", timeout=5)
        gtk = session.cookies.get("GTK")
        if gtk:
            session.headers.update({"X-Gtk": gtk})
            
        # 2. Login POST
        payload = {
            "identifiant": username,
            "motdepasse": password,
            "isReLogin": False,
            "uuid": device_uuid,
            "fa": [],
        }
        
        try:
            login_res = session.post(
                f"{API_BASE_URL}/login.awp?v={version}", 
                data={"data": json.dumps(payload)}, 
                timeout=5
            ).json()
            
            code = login_res.get("code")
            print(f"Login Response Code: {code}")
            print(f"Login Message: {login_res.get('message')}")
            
            token = login_res.get("token") or login_res.get("data", {}).get("token")
            if token:
                print(f"Got Token: {token}")
                session.headers.update({"X-Token": token})
                
                # 3. GET 2FA Question
                doubleauth_url = f"{API_BASE_URL}/connexion/doubleauth.awp?verbe=get&v={version}"
                res = session.post(doubleauth_url, data={"data": json.dumps({})}, timeout=5).json()
                print(f"2FA Question Code: {res.get('code')}")
                print(f"2FA Question Message: {res.get('message')}")
                if res.get("code") == 200:
                    print("SUCCESS! This version works!")
                    break
            else:
                print("No token received.")
        except Exception as e:
            print(f"Error during test for version {version}: {e}")

if __name__ == "__main__":
    test_versions()
