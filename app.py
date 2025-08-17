# -*- coding: utf-8 -*-
"""
Flask Web Application for Roblox Trade Ad Helper
This application uses Rolimon's official "secret phrase" verification method.
"""
from flask import Flask, render_template, jsonify, request, make_response
import requests
import time
import redis
import json
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# --- Flask App Setup ---
app = Flask(__name__, template_folder='templates', static_folder='static')

# --- Redis Cache Setup ---
redis_url = os.getenv("REDIS_HOST", "redis://localhost:6379")
# NEW: Add a print statement to debug the Redis connection URL in the logs
print(f"--> Attempting to connect to Redis at: {redis_url}")
redis_client = redis.from_url(redis_url, decode_responses=True)

# --- Constants ---
USER_SEARCH_URL = "https://users.roblox.com/v1/usernames/users"
INVENTORY_API_URL_TEMPLATE = "https://inventory.roblox.com/v1/users/{user_id}/assets/collectibles?sortOrder=Asc&limit=100&cursor="
ROLIMONS_API_URL = "https://www.rolimons.com/itemapi/itemdetails"
ROLIMONS_TRADE_AD_URL = "https://api.rolimons.com/tradeads/v1/createad"
# Official Rolimon's Auth Endpoints
ROLIMONS_GET_PHRASE_URL = "https://api.rolimons.com/auth/v1/getphrase/{player_id}"
ROLIMONS_VERIFY_PHRASE_URL = "https://api.rolimons.com/auth/v1/verifyphrase/{player_id}"
CACHE_EXPIRATION_SECONDS = 900 # 15 minutes

# --- Core Logic Functions ---

def get_user_id(username: str) -> tuple[int | None, str]:
    """Fetches the Roblox User ID for a given username."""
    payload = {"usernames": [username], "excludeBannedUsers": True}
    try:
        response = requests.post(USER_SEARCH_URL, json=payload)
        response.raise_for_status()
        data = response.json().get("data", [])
        if data:
            user_id = data[0].get("id")
            return user_id, f"User ID {user_id} found for {username}."
        else:
            return None, f"User '{username}' not found."
    except requests.exceptions.RequestException as e:
        return None, f"An API error occurred while fetching user ID: {e}"

def get_user_limiteds(user_id: int) -> tuple[list | None, str]:
    """Fetches all limited items from a user's public inventory."""
    all_items = []
    next_page_cursor = ""
    while True:
        try:
            full_url = INVENTORY_API_URL_TEMPLATE.format(user_id=user_id) + (next_page_cursor or "")
            response = requests.get(full_url)
            response.raise_for_status()
            data = response.json()
            if "data" in data and data["data"]:
                all_items.extend(data["data"])
            next_page_cursor = data.get("nextPageCursor")
            if not next_page_cursor:
                break
            time.sleep(0.25)
        except requests.exceptions.RequestException as e:
            return None, f"A network error occurred: {e}"
    return all_items, f"Successfully fetched {len(all_items)} items."

def get_all_limiteds_from_rolimons() -> tuple[list | None, str]:
    """Fetches all limited item details, using Redis as a cache."""
    cache_key = "rolimons_item_details"
    try:
        if cached_items := redis_client.get(cache_key):
            print("--> Found item details in Redis cache.")
            return json.loads(cached_items), "Fetched from cache."
    except redis.exceptions.RedisError as e:
        print(f"--- Redis Error: {e}")

    print("--> No cache found. Fetching from Rolimon's API.")
    try:
        response = requests.get(ROLIMONS_API_URL)
        response.raise_for_status()
        data = response.json()
        if data.get("success") and "items" in data:
            all_items = [{"id": item_id, "name": details[0], "rap": details[2], "value": details[3]} for item_id, details in data["items"].items()]
            try:
                redis_client.setex(cache_key, CACHE_EXPIRATION_SECONDS, json.dumps(all_items))
                print(f"--> Saved {len(all_items)} items to Redis cache.")
            except redis.exceptions.RedisError as e:
                print(f"--- Redis Error: {e}")
            return all_items, f"Fetched {len(all_items)} items from Rolimon's."
        return None, "Failed to parse response from Rolimon's API."
    except requests.exceptions.RequestException as e:
        return None, f"An error occurred while fetching from Rolimon's API: {e}"

# --- Flask Routes ---

@app.route('/')
def index():
    """Renders the main HTML page."""
    return render_template('index.html')

@app.route('/api/get-inventory/<username>')
def get_inventory_api(username):
    """API endpoint to fetch the inventory for a given username."""
    user_id, message = get_user_id(username)
    if not user_id:
        return jsonify({"success": False, "error": message}), 404
    inventory, message = get_user_limiteds(user_id)
    if inventory is None:
        return jsonify({"success": False, "error": message}), 500
    rolimons_items, _ = get_all_limiteds_from_rolimons()
    if rolimons_items:
        rolimons_map = {item['id']: item for item in rolimons_items}
        for item in inventory:
            asset_id = str(item.get("assetId"))
            rolimons_data = rolimons_map.get(asset_id)
            item['rap'] = item.get('recentAveragePrice', -1)
            item['value'] = rolimons_data.get('value', -1) if rolimons_data else -1
    sorted_inventory = sorted(inventory, key=lambda item: item.get('name', ''))
    return jsonify({"success": True, "data": sorted_inventory, "message": message, "user_id": user_id, "username": username})

@app.route('/get-all-limiteds')
def get_all_limiteds_api():
    """API endpoint to fetch all limiteds from the catalog (with caching)."""
    items, message = get_all_limiteds_from_rolimons()
    if items is None:
        return jsonify({"success": False, "error": message}), 500
    sorted_items = sorted(items, key=lambda x: x.get('name', ''))
    return jsonify({"success": True, "data": sorted_items, "message": message})

# --- Official Rolimon's Verification Routes ---

@app.route("/api/get-phrase/<int:user_id>", methods=["GET"])
def get_phrase(user_id):
    """Gets a secret phrase from Rolimon's for verification."""
    try:
        url = ROLIMONS_GET_PHRASE_URL.format(player_id=user_id)
        # CORRECTED: Changed from POST to GET
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return jsonify(resp.json()), resp.status_code
    except requests.exceptions.RequestException as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/verify-phrase/<int:user_id>", methods=["POST"])
def verify_phrase(user_id):
    """Asks Rolimon's to verify the phrase and captures the cookie."""
    try:
        url = ROLIMONS_VERIFY_PHRASE_URL.format(player_id=user_id)
        resp = requests.post(url, timeout=10)
        resp.raise_for_status()
        
        roli_verification_cookie = resp.cookies.get('_RoliVerification')
        if not roli_verification_cookie:
             return jsonify({"success": False, "error": "Verification failed: Rolimon's did not return a cookie."}), 400

        # Securely store the new cookie in Redis, linked to the user's ID
        redis_client.setex(f"user_cookie:{user_id}", 86400, roli_verification_cookie) # Expires in 24 hours
        
        return jsonify(resp.json()), resp.status_code
        
    except requests.exceptions.RequestException as e:
        error_text = e.response.json().get("message", str(e)) if e.response else str(e)
        return jsonify({"success": False, "error": error_text}), 500

@app.route("/api/post-trade-ad", methods=["POST"])
def post_trade_ad():
    """Receives trade details and posts the ad to Rolimon's using the stored cookie."""
    data = request.json
    user_id = data.get("player_id")

    # Get the user's cookie from Redis
    try:
        user_cookie = redis_client.get(f"user_cookie:{user_id}")
        if not user_cookie:
            return jsonify({"success": False, "error": "Authentication expired. Please verify again."}), 401
    except redis.exceptions.RedisError as e:
        return jsonify({"success": False, "error": f"Redis error: {e}"}), 500

    payload = {
        "player_id": user_id,
        "offer_item_ids": data.get("offer_item_ids", []),
        "request_item_ids": data.get("request_item_ids", []),
        "request_tags": data.get("request_tags", [])
    }
    headers = {"Content-Type": "application/json", "Cookie": f"_RoliVerification={user_cookie}"}

    try:
        resp = requests.post(ROLIMONS_TRADE_AD_URL, headers=headers, json=payload, timeout=10)
        resp.raise_for_status()
        return jsonify(resp.json()), resp.status_code
    except requests.exceptions.RequestException as e:
        error_text = e.response.text if e.response else str(e)
        return jsonify({"success": False, "error": error_text}), 500

# --- Main Execution ---

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)
