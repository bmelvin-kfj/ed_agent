import requests
import json
import uuid

API_BASE_URL = "https://api.ecoledirecte.com/v3"
API_VERSION = "4.96.3"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Variations to test
usernames = [
    "0757128402",
    "+33757128402",
    "33757128402",
]

passwords = [
    "MEl-vin@SGEL2011",
    "Mel-vin@SGEL2011",
    "mel-vin@SGEL2011",
    "MEL-VIN@SGEL2011",
    "Melvin@SGEL2011",
    "melvin@SGEL2011",
    "MElvin@SGEL2011",
    "MEl-vin@sgel2011",
    "Mel-vin@sgel2011",
    "mel-vin@sgel2011",
]

def test_combination(username, password):
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/x-www-form-urlencoded",
    })
    
    # Get GTK
    try:
        session.get(f"{API_BASE_URL}/login.awp?gtk=1&v={API_VERSION}", timeout=5)
        gtk = session.cookies.get("GTK")
        if gtk:
            session.headers.update({"X-Gtk": gtk})
    except Exception as e:
        print(f"[{username} / {password}] Failed to get GTK: {e}")
        return False

    payload = {
        "identifiant": username,
        "motdepasse": password,
        "isReLogin": False,
        "uuid": str(uuid.uuid4()),
        "fa": [],
    }
    
    try:
        res = session.post(
            f"{API_BASE_URL}/login.awp?v={API_VERSION}",
            data={"data": json.dumps(payload)},
            timeout=5
        ).json()
        
        code = res.get("code")
        message = res.get("message")
        token = res.get("token") or res.get("data", {}).get("token")
        
        # If it returned a token, let's test if it's valid
        if token:
            session.headers.update({"X-Token": token})
            doubleauth_url = f"{API_BASE_URL}/connexion/doubleauth.awp?verbe=get&v={API_VERSION}"
            da_res = session.post(doubleauth_url, data={"data": json.dumps({})}, timeout=5).json()
            da_code = da_res.get("code")
            da_msg = da_res.get("message")
            
            print(f"TRY: User={username} | Pass={password}")
            print(f" -> Login: Code {code} ({message})")
            print(f" -> 2FA: Code {da_code} ({da_msg})")
            
            if da_code == 200:
                print(f"\n🎉 SUCCESS! Working Credentials Found!")
                print(f"Username: {username}")
                print(f"Password: {password}")
                print(f"2FA Data: {da_res.get('data')}")
                return True
        else:
            # Code 505 or other failures without token
            pass
            
    except Exception as e:
        print(f"Error testing combination: {e}")
    return False

def main():
    print("Starting credentials diagnostic sweep...")
    found = False
    for u in usernames:
        for p in passwords:
            if test_combination(u, p):
                found = True
                break
        if found:
            break
    if not found:
        print("\n❌ Diagnostic complete. No tested combinations succeeded in fetching the 2FA question.")
        print("This strongly suggests either the username, the password, or both are completely incorrect,")
        print("or the API is strictly blocking the login requests because of too many failed attempts.")

if __name__ == "__main__":
    main()
