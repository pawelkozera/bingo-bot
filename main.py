from irc.client import SimpleIRCClient
import sys
import json
import firebase_admin
from firebase_admin import credentials, firestore
import random

# Load Twitch credentials from JSON
with open("twitch-IRC-credentials.json", "r") as config_file:
    config = json.load(config_file)

OAUTH_TOKEN = config["OAUTH_TOKEN"]
USERNAME = config["USERNAME"]
CHANNEL = config["CHANNEL"]

# Initialize Firebase
cred = credentials.Certificate("firebase-key.json")  # Replace with your actual file path
firebase_admin.initialize_app(cred)
db = firestore.client()

SENTENCES = [
    "Played a FPS game",
    "Ate on stream",
    "Donation received",
    "Technical difficulty",
    "Chat suggested strategy",
    "New subscriber",
    "Lost a match",
    "Won a match",
    "Used a meme phrase",
    "Read chat aloud"
]

class TwitchChatListener(SimpleIRCClient):
    def __init__(self):
        super().__init__()
        print("[DEBUG] TwitchChatListener initialized.")

    def on_connect(self, connection, event):
        print("[DEBUG] Connected to Twitch IRC server.")

    def on_welcome(self, connection, event):
        print("[DEBUG] Successfully authenticated, joining channel...")
        connection.join(CHANNEL)

    def on_join(self, connection, event):
        print(f"[DEBUG] Successfully joined {CHANNEL}")

    def on_pubmsg(self, connection, event):
        username = event.source.split("!")[0]
        message = event.arguments[0]
        print(f"[CHAT] {username}: {message}")

        if message == "!bingojoin":
            print(f"[COMMAND] {username} requested to join Bingo")
            self.create_bingo_card(username)

    def create_bingo_card(self, username):
        """Generate and store a Bingo card for the user"""
        try:
            # Create a unique card with 9 random sentences
            card = random.sample(SENTENCES, 9)
            
            # Store in Firestore
            db.collection("users").document(username).set({
                "card": card,
                "marked": [False, False, False, False, False, False, False, False, False]
            })
            print(f"[FIREBASE] Created card for {username}")
            
        except Exception as e:
            print(f"[ERROR] Failed to create card for {username}: {e}")

    def on_ping(self, connection, event):
        print("[DEBUG] Received PING, sending PONG...")
        connection.pong(event.arguments[0])

    def on_disconnect(self, connection, event):
        print("[ERROR] Disconnected from Twitch, retrying...")
        sys.exit(1)

    def start(self):
        try:
            print("[DEBUG] Connecting to Twitch...")
            self.connect("irc.chat.twitch.tv", 6667, USERNAME, password=OAUTH_TOKEN)
            print("[DEBUG] Connected, processing messages...")
            self.reactor.process_forever()
        except Exception as e:
            print(f"[ERROR] Connection failed: {e}")

if __name__ == "__main__":
    client = TwitchChatListener()
    client.start()
