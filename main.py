from irc.client import SimpleIRCClient
import sys
import json
import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore import FieldFilter
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
bingo_user_ref = db.collection("streamer").document(CHANNEL[1:]).collection("game_name").document("bingo")

def get_user_ref(username):
    return bingo_user_ref.collection("players").document(username)

def get_bingo_questions():
    questions_ref = db.collection("streamer").document(CHANNEL[1:]) \
        .collection("game_name").document("bingo") \
        .collection("questions")
    
    docs = questions_ref.stream()
    return [{"id": doc.id, **doc.to_dict()} for doc in docs]

def get_bingo_settings():
    doc_ref = db.collection("streamer").document(CHANNEL[1:]) \
        .collection("game_name").document("bingo")
    
    doc = doc_ref.get()
    if doc.exists:
        return doc.to_dict()
    return {
        "grid_rows": 5,
        "grid_columns": 5,
    }

questions = get_bingo_questions()
print(f"Loaded {len(questions)} questions from subcollection")

settings = get_bingo_settings()
print(f"Grid size: {settings['grid_rows']}x{settings['grid_columns']}")


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

        if username == "p0js" or username == "je1lybeann":
            if message.lower() == "!bingostart" or message.lower() == "!bingoactivate":
                self.activate_bingo(connection, True)
                print(f"[COMMAND] The bingo game is now active!")
                return
            elif message.lower() == "!bingoend" or message.lower() == "!bingostop":
                self.activate_bingo(connection, False)
                print(f"[COMMAND] The bingo game ended!")
                return

        if not self.is_game_active():
            print(f"[BLOCKED] The bingo game is not currently active!")
            return

        if message.lower().startswith("!bingo") and self.has_user_won(username):
            print(f"[BLOCKED] {username} already won.")
            return

        if message.lower() == "!bingojoin":
            print(f"[COMMAND] {username} requested to join Bingo")
            self.create_bingo_card(username)
        elif message.lower().startswith("!bingocheck"):
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
    
    def activate_bingo(self, connection, activate=True):
        try:
            channel = CHANNEL[1:]  # Remove '#' from channel name
            bingo_ref = db.collection("streamer").document(channel).collection("game_name").document("bingo")
            batch = db.batch()

            if activate:
                # Reset game state when starting
                batch.update(bingo_ref, {
                    "isActive": True,
                    "numberOfBingosToCheck": 0,
                    "bingoApprovedCount": 0
                })

                settings.set('numberOfBingosToCheck', 0)

                # Delete all existing players
                players_ref = bingo_ref.collection("players")
                for doc in players_ref.stream():
                    batch.delete(doc.reference)

            else:
                # Reset counters when stopping
                batch.update(bingo_ref, {
                    "isActive": False,
                })

            # Commit the batch
            batch.commit()
            
            # Send chat message
            msg = "Bingo game started! 🎉 Please type !bingojoin to participate!" if activate \
                else "Bingo game ended! 🎉 Thanks for playing!"
            connection.privmsg(CHANNEL, msg)

        except Exception as e:
            print(f"[ERROR] Failed to {'activate' if activate else 'deactivate'} game: {e}")
    
    def is_game_active(self):
        """Check if the game is marked as active in Firestore"""
        try:
            # Get the bingo document reference
            bingo_ref = db.collection("streamer").document(CHANNEL[1:]).collection("game_name").document("bingo")
            doc = bingo_ref.get()
            return doc.to_dict().get("isActive", False) if doc.exists else False
        except Exception as e:
            print(f"[ERROR] Failed to check game status: {e}")
            return False
    
    def has_user_won(self, username):
        """Check if user has already won"""
        try:
            user_ref = get_user_ref(username)
            user_doc = user_ref.get()

            if not user_doc.exists:
                return False  # User doesn't have a bingo card

            user_data = user_doc.to_dict()
            return user_data.get("isBingo", False) or user_data.get("markedForCheck", False)  # Return True if they have won
        except Exception as e:
            print(f"[ERROR] Failed to check bingo status for {username}: {e}")
            return False

    def create_bingo_card(self, username):
        """Generate and store a Bingo card using questions from Firestore"""
        try:
            user_ref = bingo_user_ref.collection("players").document(username)
            if user_ref.get().exists:
                print(f"[INFO] Card already exists for {username}")
                return
        
            # Get available questions (filter unused ones)
            questions_ref = bingo_user_ref.collection("questions")
            query = questions_ref.where(filter=FieldFilter("isUsed", "==", True))  # Fixed filter syntax
            query_result = query.stream()
            
            available_questions = []
            for doc in query_result:
                data = doc.to_dict()
                if "text" in data:
                    available_questions.append(data["text"])
                else:
                    print(f"[WARNING] Question {doc.id} is missing 'text' field")

            if not available_questions:
                raise ValueError("No available questions in the pool")

            # Get grid size from settings
            settings = bingo_user_ref.get().to_dict()
            grid_rows = int(settings.get("grid_rows", 5))
            grid_columns = int(settings.get("grid_columns", 5))
            card_size = grid_rows * grid_columns

            print(f"[DEBUG] Found {len(available_questions)} questions, need {card_size}")

            if len(available_questions) < card_size:
                raise ValueError(f"Need {card_size} questions, only {len(available_questions)} available")

            # Create random card and mark questions as used
            card = random.sample(available_questions, card_size)
            
            # Store in Firestore
            user_ref = bingo_user_ref.collection("players").document(username)
            user_ref.set({
                "card": card,
                "marked": [False] * card_size,
                "showCard": True,
                "isBingo": False,
                "markedForCheck": False,
                "numberForApproval": 0,
                "gameEnded": False
            })

            print(f"[FIREBASE] Created {grid_rows}x{grid_columns} card for {username}")

        except Exception as e:
            print(f"[ERROR] Failed to create card for {username}: {str(e)}")
            raise
    
    def handle_bingocheck(self, connection, username, number_str, mark=True):
        """Process !bingocheck command"""
        try:
            # Get references
            user_ref = get_user_ref(username)
            bingo_settings_ref = bingo_user_ref  # Your main bingo doc reference

            # Run transaction to prevent race conditions
            @firestore.transactional
            def update_approval_queue(transaction, user_ref, bingo_settings_ref):
                # Get current counter
                grid_rows = int(settings.get("grid_rows", 5))
                grid_columns = int(settings.get("grid_columns", 5))
                current_count = int(settings.get('numberOfBingosToCheck', 0))

                # Get user data
                user_doc = user_ref.get(transaction=transaction)
                if not user_doc.exists:
                    return False

                user_data = user_doc.to_dict()
                max_number = grid_columns * grid_rows

                # Validate input
                number = int(number_str)
                if not (1 <= number <= max_number):
                    return False

                # Update marked status
                index = number - 1
                marked = user_data["marked"]

                if marked[index] and not mark:
                    # Handle unmarking
                    marked[index] = mark
                    transaction.update(user_ref, {"marked": marked, "showCard": True})
                    return False

                if not marked[index]:
                    marked[index] = mark

                # Check for win and update approval queue
                if self.check_bingo(marked, grid_columns, grid_rows):
                    new_count = current_count + 1
                    # Update both documents atomically
                    transaction.update(bingo_settings_ref, {
                        'numberOfBingosToCheck': firestore.Increment(1)
                    })
                    transaction.update(user_ref, {
                        "marked": marked,
                        "showCard": True,
                        "markedForCheck": True,
                        "numberForApproval": new_count
                    })
                    return True
                else:
                    transaction.update(user_ref, {
                        "marked": marked,
                        "showCard": True
                    })
                    return False

            # Run the transaction
            transaction = db.transaction()
            success = update_approval_queue(transaction, user_ref, bingo_settings_ref)

            if success:
                connection.privmsg(CHANNEL, f"🎉 Card submitted for approval {username}! 🎉")

        except Exception as e:
            print(f"[ERROR] {e}")
    
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
            user_ref = get_user_ref(username)
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
