import logging
import os
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template
import boto3
import razorpay
from dotenv import load_dotenv
import hmac
import hashlib
import base64

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO)

# Retrieve configuration from environment variables
AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")
DYNAMODB_TABLE_NAME = os.getenv("DYNAMODB_TABLE_NAME", "UserSubscriptions")
RAZORPAY_API_KEY = os.getenv("RAZORPAY_API_KEY")
RAZORPAY_API_SECRET = os.getenv("RAZORPAY_API_SECRET")

# Initialize AWS DynamoDB client (not used in callback now)
dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
table = dynamodb.Table(DYNAMODB_TABLE_NAME)

# Initialize Razorpay client
razorpay_client = razorpay.Client(auth=(RAZORPAY_API_KEY, RAZORPAY_API_SECRET))

# Define subscription plans in days (still used in subscribe endpoint)
PLAN_PERIODS = {
    'monthly': 30,
    'quarterly': 90,
    'yearly': 365
}

def calculate_new_expiry(current_expiry, plan_type):
    """Calculate new subscription expiry date."""
    days = PLAN_PERIODS.get(plan_type.lower(), 30)
    now = datetime.now()
    
    if current_expiry and current_expiry > now:
        new_expiry = current_expiry + timedelta(days=days)
    else:
        new_expiry = now + timedelta(days=days)
    
    return new_expiry

@app.route('/')
def index():
    """Serve the index.html page."""
    return render_template('index.html')

@app.route('/subscribe', methods=['POST'])
def subscribe():
    """Create a Razorpay order for subscription."""
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        plan_type = data.get('plan_type')
        amount = data.get('amount')  # in INR

        if not (user_id and plan_type and amount):
            return jsonify({"error": "Missing required fields"}), 400

        # Convert amount to integer (paise)
        try:
            amount_in_paise = int(float(amount) * 100)
        except ValueError:
            return jsonify({"error": "Invalid amount format"}), 400

        # Create an order in Razorpay
        order_data = {
            'amount': amount_in_paise,  
            'currency': 'INR',
            'receipt': str(user_id),
            'payment_capture': 1  # Auto capture payment
        }

        razorpay_order = razorpay_client.order.create(data=order_data)

        return jsonify({
            "order_id": razorpay_order['id'],
            "message": "Order created successfully. Please proceed with payment." 
        })

    except Exception as e:
        logging.error(f"Error creating order: {str(e)}")
        return jsonify({"error": "Error creating order", "details": str(e)}), 500


@app.route('/payment-callback', methods=['POST'])
def payment_callback():
    """Handle Razorpay payment callback: verify signature and extract parameters."""
    try:
        webhook_secret = os.getenv("WEBHOOK_SECRET")
        if not webhook_secret:
            logging.error(" WEBHOOK_SECRET is missing in environment variables")
            return jsonify({"error": "Webhook secret not configured"}), 500

        # Obtain the raw payload as a string
        payload_str = request.get_data(as_text=True)
        received_signature = str(request.headers.get('X-Razorpay-Signature', ''))

        # Debug: log raw data and headers
        logging.info(f"Raw Webhook Data: {payload_str}")
        logging.info(f"Webhook Headers: {dict(request.headers)}")

        if not received_signature:
            logging.error(" No Razorpay signature received")
            return jsonify({"error": "No Razorpay signature"}), 400

        # Compute signature locally for debugging
        computed_digest = hmac.new(
            webhook_secret.encode('utf-8'),
            payload_str.encode('utf-8'),
            hashlib.sha256
        ).digest()
        computed_signature = computed_digest.hex()
        logging.info(f"Computed Signature: {computed_signature}")
        logging.info(f"Received Signature: {received_signature}")    # needs to be checked

        # Verify the webhook signature using Razorpay's utility
        try:
            razorpay_client.utility.verify_webhook_signature(payload_str, received_signature, webhook_secret)
            logging.info(" Webhook signature verification successful")
        except razorpay.errors.SignatureVerificationError as e:
            logging.error(f" Signature verification failed: {str(e)}")
            return jsonify({"error": "Signature verification failed"}), 400

        # Parse the JSON payload
        data = request.get_json()
        logging.info(f"Received payment callback data: {data}")

        # Extract essential parameters (only payment_id and order_id are mandatory here)
        # payment_id = data.get('payload', {}).get('payment', {}).get('entity', {}).get('id')
        # order_id = data.get('payload', {}).get('payment', {}).get('entity', {}).get('order_id')
        
        payment_id = data.get('razorpay_payment_id')
        order_id = data.get('razorpay_order_id')

        # Log optional fields if they exist
        user_id = data.get('user_id', 'N/A')
        plan_type = data.get('plan_type', 'N/A')
        logging.info(f"User ID: {user_id}, Plan Type: {plan_type}")

        if not all([payment_id, order_id]):
            logging.error(" Missing required payment fields in webhook data")
            return jsonify({"error": "Missing required fields"}), 400

        # Return success response after verifying signature and parameters
        return jsonify({"message": "Webhook signature verified successfully"}), 200

    except Exception as e:
        logging.error(f" Error verifying webhook: {str(e)}")
        return jsonify({"error": "Error verifying webhook", "details": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)