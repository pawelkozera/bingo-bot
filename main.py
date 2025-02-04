from irc.client import SimpleIRCClient
import sys
import json
import firebase_admin
from firebase_admin import credentials, firestore
import random
import math

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
    "Pikachu appearance",
    "Panther appearance",
    "Sensei meeting",
    "Pee break",
    "Team mute",
    "Hating on men",
    "Someone has been scammed",
    "Won a match",
    "Alcoholic hiccup",
    "Gambling addiction"
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

        if message.lower() == "!bingojoin":
            print(f"[COMMAND] {username} requested to join Bingo")
            self.create_bingo_card(username)

        if self.has_user_won(username):
            print(f"[BLOCKED] {username} already won. Only !bingojoin is allowed.")
            return 
        
        if message.lower().startswith("!bingocheck"):
            parts = message.split()
            print(f"[COMMAND] {username} used bingocheck {parts[1]}")
            if len(parts) < 2:
                return
            self.handle_bingocheck(connection, username, parts[1], True)
        elif message.lower().startswith("!bingouncheck"):
            parts = message.split()
            print(f"[COMMAND] {username} used unbingocheck {parts[1]}")
            if len(parts) < 2:
                return
            self.handle_bingocheck(connection, username, parts[1], False)
        elif message.lower() == "!bingoshow":
            print(f"[COMMAND] {username} requested to show their card")
            self.handle_bingoshow(connection, username)
            return
    
    def has_user_won(self, username):
        """Check if user has already won"""
        try:
            user_ref = db.collection("users").document(username)
            user_doc = user_ref.get()

            if not user_doc.exists:
                return False  # User doesn't have a bingo card

            user_data = user_doc.to_dict()
            return user_data.get("isBingo", False)  # Return True if they have won
        except Exception as e:
            print(f"[ERROR] Failed to check bingo status for {username}: {e}")
            return False

    def create_bingo_card(self, username):
        """Generate and store a Bingo card for the user"""
        try:
            # Create a unique card with random sentences
            card_size = 9  # Change this to any perfect square (4, 9, 16, 25, etc.)
            card = random.sample(SENTENCES, card_size)
            
            # Calculate grid dimensions for square grid
            grid_side = math.isqrt(card_size)
            if grid_side ** 2 != card_size:
                raise ValueError("Card size must be a perfect square for square grid")
            
            # Store in Firestore
            db.collection("users").document(username).set({
                "card": card,
                "marked": [False] * card_size,
                "grid_columns": grid_side,
                "grid_rows": grid_side,
                "showCard": True,
                "isBingo": False
            })
            print(f"[FIREBASE] Created {grid_side}x{grid_side} card for {username}")
            
        except Exception as e:
            print(f"[ERROR] Failed to create card for {username}: {e}")
    
    def handle_bingocheck(self, connection, username, number_str, mark = True):
        """Process !bingocheck command"""
        try:
            # Get user data
            user_ref = db.collection("users").document(username)
            user_doc = user_ref.get()
            
            if not user_doc.exists:
                return

            user_data = user_doc.to_dict()
            grid_columns = user_data["grid_columns"]
            grid_rows = user_data["grid_rows"]
            max_number = grid_columns * grid_rows

            # Validate input
            number = int(number_str)
            if not (1 <= number <= max_number):
                print(f"[ERROR] {username} typed wrong number {number}")
                return

            # Update marked status
            index = number - 1
            marked = user_data["marked"]
            if marked[index]:
                if (not mark):
                    marked[index] = mark
                    user_ref.update({"marked": marked, "showCard": True})
                    print(f"[FIREBASE] {username} unmarked position {index}")
                return

            marked[index] = mark
            # Check for win
            if self.check_bingo(marked, grid_columns, grid_rows):
                print(f"[FIREBASE] Bingo! {username}")
                connection.privmsg(CHANNEL, f"🎉 BINGO {username}! 🎉")
                user_ref.update({"marked": marked, "showCard": True, "isBingo": True})
            else:
                print(f"[FIREBASE] {username} marked position {index}")
                user_ref.update({"marked": marked, "showCard": True})
    
        except Exception as e:
            print(f"[ERROR] {e}")
            return
    
    def check_bingo(self, marked, grid_columns=3, grid_rows=3):
        """
        Check for Bingo in flexible grid sizes.
        Default: 3x3 grid (3 columns, 3 rows)
        """
        # Validate grid dimensions match marked array size
        if len(marked) != grid_columns * grid_rows:
            print(f"[ERROR] Grid {grid_columns}x{grid_rows} doesn't match {len(marked)} marked slots")
            return False

        # Convert to 2D grid (list of rows)
        grid = []
        for i in range(grid_rows):
            start = i * grid_columns
            end = start + grid_columns
            grid.append(marked[start:end])

        # Check horizontal lines
        for row in grid:
            if all(row):
                return True

        # Check vertical lines
        for col in range(grid_columns):
            if all(grid[row][col] for row in range(grid_rows)):
                return True

        # Check diagonals (only if square grid)
        if grid_columns == grid_rows:
            # Primary diagonal (top-left to bottom-right)
            if all(grid[i][i] for i in range(grid_columns)):
                return True
            
            # Secondary diagonal (top-right to bottom-left)
            if all(grid[i][grid_columns-1-i] for i in range(grid_columns)):
                return True

        return False

    def handle_bingoshow(self, connection, username):
        """Display user's bingo card in chat"""
        try:
            user_ref = db.collection("users").document(username)
            user_doc = user_ref.get()

            if not user_doc.exists:
                return

            user_data = user_doc.to_dict()
            card = user_data.get("card", [])
            marked = user_data.get("marked", [])
            
            card_text = ", ".join(
                [f"{i+1}: {'✅ ' if marked[i] else ''}{text}" 
                for i, text in enumerate(card)]
            )

            connection.privmsg(CHANNEL, f"{username}'s Bingo Card: {card_text[:400]}")

        except Exception as e:
            print(f"[ERROR] Failed to show card for {username}: {e}")

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
