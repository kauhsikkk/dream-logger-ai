from flask import Flask, request, jsonify, send_from_directory, render_template, redirect, url_for, session
from flask_cors import CORS
import requests
import os
import json
import time
import random
import sqlite3
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'dream_logger_secret_key_12345'  # For session management
CORS(app)

# Configure Gemini API
GEMINI_API_KEY = "AIzaSyBL-W-vhUYNTs-HBXZPGze-y-xXdEZYHzg"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

def call_gemini_api(prompt):
    """Call Gemini API using REST endpoint"""
    headers = {
        'Content-Type': 'application/json',
        'X-goog-api-key': GEMINI_API_KEY
    }

    data = {
        "contents": [
            {
                "parts": [
                    {
                        "text": prompt
                    }
                ]
            }
        ]
    }

    try:
        response = requests.post(GEMINI_API_URL, headers=headers, json=data, timeout=30)
        if response.status_code == 200:
            result = response.json()
            if 'candidates' in result and len(result['candidates']) > 0:
                return result['candidates'][0]['content']['parts'][0]['text']
        else:
            print(f"Gemini API error: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        print(f"Gemini API call failed: {e}")
        return None

# Keep existing Hugging Face setup for image generation only
HUGGINGFACE_API_TOKEN = "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"
HEADERS = {
    "Authorization": f"Bearer {HUGGINGFACE_API_TOKEN}",
    "Content-Type": "application/json"
} if HUGGINGFACE_API_TOKEN else {}

# Database initialization
def init_db():
    conn = sqlite3.connect('dreams.db')
    cursor = conn.cursor()

    # Create users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Check if dreams table exists and get its schema
    cursor.execute("PRAGMA table_info(dreams)")
    columns = [column[1] for column in cursor.fetchall()]

    if 'dreams' not in [table[0] for table in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]:
        # Create new dreams table
        cursor.execute('''
            CREATE TABLE dreams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                dream_text TEXT NOT NULL,
                mood TEXT,
                interpretation TEXT,
                image_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (username) REFERENCES users (username)
            )
        ''')
    elif 'username' not in columns:
        # Migrate old table - rename and recreate
        cursor.execute('ALTER TABLE dreams RENAME TO dreams_old')
        cursor.execute('''
            CREATE TABLE dreams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL DEFAULT 'anonymous',
                dream_text TEXT NOT NULL,
                mood TEXT,
                interpretation TEXT,
                image_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (username) REFERENCES users (username)
            )
        ''')
        # Migrate data if old table had data
        cursor.execute('''
            INSERT INTO dreams (dream_text, mood, interpretation, image_url, created_at)
            SELECT dream_text, mood, interpretation, image_url, created_at FROM dreams_old
        ''')
        cursor.execute('DROP TABLE dreams_old')

    conn.commit()
    conn.close()

# Initialize database on startup
init_db()

# Ensure static folder exists
os.makedirs("static", exist_ok=True)

@app.route("/")
def home():
    # Check if user is logged in
    if 'username' in session:
        return render_template('dashboard.html', username=session['username'])
    else:
        return render_template('login.html')

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        data = request.get_json()
        username = data.get("username", "").strip()

        if not username:
            return jsonify({"error": "Username is required"}), 400

        if len(username) < 3:
            return jsonify({"error": "Username must be at least 3 characters"}), 400

        # Check if username exists, if not create it
        conn = sqlite3.connect('dreams.db')
        cursor = conn.cursor()

        cursor.execute('SELECT username FROM users WHERE username = ?', (username,))
        user = cursor.fetchone()

        if not user:
            # Create new user
            try:
                cursor.execute('INSERT INTO users (username) VALUES (?)', (username,))
                conn.commit()
                print(f"✅ Created new user: {username}")
            except sqlite3.IntegrityError:
                conn.close()
                return jsonify({"error": "Username already taken"}), 400

        conn.close()

        # Log in the user
        session['username'] = username
        return jsonify({"success": True, "username": username})

    return render_template('login.html')

@app.route("/logout")
def logout():
    session.pop('username', None)
    return redirect(url_for('home'))

@app.route("/dashboard")
def dashboard():
    if 'username' not in session:
        return redirect(url_for('login'))
    return render_template('dashboard.html', username=session['username'])

@app.route("/dreams")
def get_dreams():
    if 'username' not in session:
        return jsonify({"error": "Authentication required"}), 401

    username = session['username']

    conn = sqlite3.connect('dreams.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, dream_text, mood, interpretation, image_url, created_at
        FROM dreams 
        WHERE username = ? 
        ORDER BY created_at DESC
    ''', (username,))

    dreams = []
    for row in cursor.fetchall():
        dreams.append({
            "id": row[0],
            "dream_text": row[1],
            "mood": row[2],
            "interpretation": row[3],
            "image_url": row[4],
            "created_at": row[5]
        })

    conn.close()
    return jsonify(dreams)

@app.route("/<path:filename>")
def serve_static(filename):
    return send_from_directory(".", filename)

@app.route("/analyze", methods=["POST"])
def analyze():
    try:
        # Check authentication
        if 'username' not in session:
            return jsonify({"error": "Authentication required"}), 401

        username = session['username']
        data = request.get_json()
        dream_text = data.get("dream", "")

        if not dream_text:
            return jsonify({"error": "No dream text provided"}), 400

        print(f"Analyzing dream for {username}: {dream_text[:100]}...")

        # Use Gemini for mood detection and interpretation
        mood = detect_mood_with_gemini(dream_text)
        interpretation = interpret_dream_with_gemini(dream_text)

        # Keep original image generation
        image_url = generate_image(dream_text)

        # Save to database
        conn = sqlite3.connect('dreams.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO dreams (username, dream_text, mood, interpretation, image_url)
            VALUES (?, ?, ?, ?, ?)
        ''', (username, dream_text, mood, interpretation, image_url))
        conn.commit()
        conn.close()

        return jsonify({
            "mood": mood,
            "interpretation": interpretation,
            "image": image_url
        })

    except Exception as e:
        print(f"Error in analyze endpoint: {e}")
        return jsonify({"error": "Analysis failed, please try again"}), 500

def detect_mood_with_gemini(dream_text):
    """Use Gemini API to detect mood from dream text"""
    try:
        prompt = f"""
        Analyze the emotional tone and mood of this dream description. 
        Return ONLY ONE of these four mood categories: Excited, Anxious, Calm, or Mysterious

        Dream: {dream_text}

        Mood category:
        """

        result = call_gemini_api(prompt)
        if result:
            mood_result = result.strip()

            # Validate the response
            valid_moods = ["Excited", "Anxious", "Calm", "Mysterious"]
            for mood in valid_moods:
                if mood.lower() in mood_result.lower():
                    return mood

        # If no valid mood found, use fallback
        return fallback_mood_detection(dream_text)

    except Exception as e:
        print(f"Gemini mood detection failed: {e}")
        return fallback_mood_detection(dream_text)

def fallback_mood_detection(dream_text):
    """Enhanced keyword-based mood detection"""
    text_lower = dream_text.lower()

    # Enhanced keyword lists
    excited_words = ['flying', 'soar', 'beautiful', 'amazing', 'wonderful', 'joy', 'happy', 'love', 'success', 'win', 'celebration', 'bright', 'golden', 'magical', 'adventure', 'discovery']
    anxious_words = ['falling', 'chase', 'scary', 'fear', 'nightmare', 'dark', 'lost', 'trapped', 'monster', 'danger', 'death', 'hurt', 'pain', 'scream', 'hide', 'run']
    calm_words = ['peaceful', 'quiet', 'serene', 'gentle', 'soft', 'floating', 'garden', 'nature', 'water', 'meditation', 'rest', 'comfortable']
    mysterious_words = ['strange', 'weird', 'unknown', 'mysterious', 'bizarre', 'surreal', 'abstract', 'symbols', 'riddle', 'puzzle']

    # Count occurrences
    excited_count = sum(1 for word in excited_words if word in text_lower)
    anxious_count = sum(1 for word in anxious_words if word in text_lower)
    calm_count = sum(1 for word in calm_words if word in text_lower)
    mysterious_count = sum(1 for word in mysterious_words if word in text_lower)

    # Determine mood
    scores = {
        'Excited': excited_count,
        'Anxious': anxious_count,
        'Calm': calm_count,
        'Mysterious': mysterious_count
    }

    # Find the mood with highest score
    max_score = max(scores.values())
    if max_score > 0:
        for mood, score in scores.items():
            if score == max_score:
                return mood

    return "Mysterious"

def interpret_dream_with_gemini(dream_text):
    """Use Gemini API to interpret dreams"""
    try:
        prompt = f"""
        You are a mystical dream interpreter. Analyze this dream and provide:
        1. A poetic, mystical interpretation (2-3 sentences)
        2. A personalized dream scene story (4-5 sentences that retell the dream as an enchanted narrative)

        Format your response as:
        INTERPRETATION: [your mystical interpretation]
        SCENE: [your dream scene story]

        Dream to interpret: {dream_text}
        """

        result = call_gemini_api(prompt)
        if result:
            # Parse the response
            interpretation_text = ""
            scene_text = ""

            if "INTERPRETATION:" in result and "SCENE:" in result:
                parts = result.split("SCENE:")
                interpretation_text = parts[0].replace("INTERPRETATION:", "").strip()
                scene_text = parts[1].strip()
            else:
                # If format is not followed, use the whole response as interpretation
                interpretation_text = result
                scene_text = generate_dream_scene(dream_text)

            return f"{interpretation_text}\n\n📖 **Dream Scene**: {scene_text}"
        else:
            return fallback_interpretation_with_scene(dream_text)

    except Exception as e:
        print(f"Gemini interpretation failed: {e}")
        return fallback_interpretation_with_scene(dream_text)

def fallback_interpretation_with_scene(dream_text):
    """Fallback interpretation when Gemini is not available"""
    interpretation_text = "Your dream echoes with mysterious symbols. It whispers wisdom wrapped in shadows, revealing hidden aspects of your subconscious mind."
    story = generate_dream_scene(dream_text)
    return f"{interpretation_text}\n\n📖 **Dream Scene**: {story}"

def generate_dream_scene(dream_text):
    """Generate a personalized mini story based on the dream content"""
    text_lower = dream_text.lower()

    # Extract key elements from the dream
    characters = []
    locations = []
    actions = []
    objects = []
    emotions = []

    # Character detection
    if any(word in text_lower for word in ['person', 'people', 'man', 'woman', 'child', 'friend', 'family', 'stranger']):
        characters.append("mysterious figures")
    if any(word in text_lower for word in ['animal', 'dog', 'cat', 'bird', 'snake', 'lion', 'wolf']):
        characters.append("spirit animals")
    if any(word in text_lower for word in ['monster', 'demon', 'ghost', 'shadow']):
        characters.append("shadow beings")

    # Location detection
    if any(word in text_lower for word in ['flying', 'sky', 'clouds', 'air', 'high']):
        locations.append("floating through ethereal skies")
    if any(word in text_lower for word in ['water', 'ocean', 'sea', 'river', 'swimming']):
        locations.append("diving through crystal waters")
    if any(word in text_lower for word in ['forest', 'trees', 'woods', 'jungle']):
        locations.append("wandering through enchanted forests")
    if any(word in text_lower for word in ['house', 'home', 'building', 'room']):
        locations.append("exploring shifting architectural wonders")
    if any(word in text_lower for word in ['city', 'street', 'road', 'urban']):
        locations.append("navigating through dreamscape cities")
    if any(word in text_lower for word in ['school', 'class', 'teacher', 'student']):
        locations.append("discovering halls of infinite learning")
    if any(word in text_lower for word in ['car', 'driving', 'vehicle', 'road']):
        locations.append("journeying on roads that bend reality")

    # Action detection
    if any(word in text_lower for word in ['running', 'chase', 'escape', 'flee']):
        actions.append("racing through dimensions")
    if any(word in text_lower for word in ['falling', 'drop', 'descend']):
        actions.append("descending through layers of consciousness")
    if any(word in text_lower for word in ['climbing', 'up', 'ascend', 'rise']):
        actions.append("ascending toward illuminated peaks")
    if any(word in text_lower for word in ['lost', 'searching', 'looking', 'find']):
        actions.append("seeking hidden truths")
    if any(word in text_lower for word in ['talking', 'speaking', 'conversation']):
        actions.append("exchanging wisdom with cosmic entities")

    # Object detection
    if any(word in text_lower for word in ['door', 'gate', 'entrance', 'portal']):
        objects.append("mystical doorways")
    if any(word in text_lower for word in ['mirror', 'reflection', 'image']):
        objects.append("mirrors revealing alternate selves")
    if any(word in text_lower for word in ['light', 'bright', 'glow', 'shine']):
        objects.append("sources of otherworldly light")
    if any(word in text_lower for word in ['book', 'writing', 'text', 'letter']):
        objects.append("ancient texts written in starlight")

    # Emotion detection
    if any(word in text_lower for word in ['scared', 'afraid', 'fear', 'terror']):
        emotions.append("waves of primal fear transforming into courage")
    if any(word in text_lower for word in ['happy', 'joy', 'excited', 'love']):
        emotions.append("currents of pure bliss")
    if any(word in text_lower for word in ['sad', 'cry', 'tears', 'sorrow']):
        emotions.append("healing tears that nourish dream gardens")
    if any(word in text_lower for word in ['angry', 'mad', 'rage', 'furious']):
        emotions.append("fire energy burning away old patterns")

    # Build the story
    story_parts = []

    # Opening
    if locations:
        story_parts.append(f"In this vision, you found yourself {random.choice(locations)}")
    else:
        story_parts.append("In this mystical realm, you began a journey through landscapes beyond imagination")

    # Characters and interactions
    if characters:
        story_parts.append(f"Accompanied by {random.choice(characters)}, you discovered hidden meanings in every encounter")

    # Actions and movement
    if actions:
        story_parts.append(f"As the dream unfolded, you were {random.choice(actions)}, each movement revealing deeper layers of your subconscious")

    # Objects and symbols
    if objects:
        story_parts.append(f"Throughout the experience, {random.choice(objects)} appeared as guides, offering glimpses into your inner wisdom")

    # Emotional transformation
    if emotions:
        story_parts.append(f"The journey carried {random.choice(emotions)}, teaching your soul through the language of dreams")
    else:
        story_parts.append("The experience wove threads of transformation through your sleeping consciousness")

    # Closing
    story_parts.append("When you awakened, the dream's essence lingered, whispering insights for your waking life")

    return ". ".join(story_parts) + "."

def generate_image(prompt):
    """Keep original Hugging Face image generation"""
    models = [
        "stabilityai/stable-diffusion-xl-base-1.0",
        "Lykon/dreamshaper-8"
    ]
    formatted_prompt = f"surreal dream, {prompt[:100]}, ethereal, mystical, fantasy, vivid colors"
    for model in models:
        try:
            url = f"https://api-inference.huggingface.co/models/{model}"
            response = requests.post(url, headers=HEADERS, json={"inputs": formatted_prompt}, timeout=60)
            if response.status_code == 200 and response.headers.get("content-type", "").startswith("image"):
                image_path = f"static/dream_image_{int(time.time())}.png"
                with open(image_path, "wb") as f:
                    f.write(response.content)
                return "/" + image_path
        except Exception as e:
            print(f"Image generation failed for {model}: {e}")
    return f"https://picsum.photos/400/300?random={random.randint(1,9999)}"

if __name__ == "__main__":
    print("🌙 Starting Dream Logger AI with Gemini API...")

    # Test Gemini API
    test_result = call_gemini_api("Hello, are you working?")
    if test_result:
        print("✅ Gemini API ready for mood detection and dream interpretation")
    else:
        print("⚠️  Gemini API not responding - using fallback methods")
        print("💡 Check your API key and internet connection")

    print("🚀 Server will be available at http://0.0.0.0:5000")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)

