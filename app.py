import logging
import os
from datetime import datetime, timedelta
import boto3
import razorpay
import hmac
import hashlib
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

# Load environment variables
load_dotenv()

# Initialize FastAPI app
app = FastAPI()

# Configure logging
logging.basicConfig(level=logging.INFO)

# Retrieve configuration from environment variables
AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")
DYNAMODB_TABLE_NAME = os.getenv("DYNAMODB_TABLE_NAME", "UserSubscriptions")
RAZORPAY_API_KEY = os.getenv("RAZORPAY_API_KEY")
RAZORPAY_API_SECRET = os.getenv("RAZORPAY_API_SECRET")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET")
BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:5000")

# Initialize AWS DynamoDB client
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
table = dynamodb.Table(DYNAMODB_TABLE_NAME)

# Initialize Razorpay client
razorpay_client = razorpay.Client(auth=(RAZORPAY_API_KEY, RAZORPAY_API_SECRET))

# Subscription plan durations
PLAN_PERIODS = {
    "monthly": 30,
    "quarterly": 90,
    "yearly": 365
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


@app.get("/")
async def index():
    return {"message": f"Server is running at {BASE_URL}"}


@app.post("/subscribe")
async def subscribe(request: Request):
    """Create a Razorpay order for subscription."""
    try:
        data = await request.json()
        user_id = data.get("user_id")
        plan_type = data.get("plan_type")
        amount = data.get("amount")  # in INR

        if not (user_id and plan_type and amount):
            raise HTTPException(status_code=400, detail="Missing required fields")

        # Convert amount to paise (integer)
        try:
            amount_in_paise = int(float(amount) * 100)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid amount format")

        # Create an order in Razorpay
        order_data = {
            "amount": amount_in_paise,
            "currency": "INR",
            "receipt": str(user_id),
            "payment_capture": 1,  # Auto capture payment
        }

        razorpay_order = razorpay_client.order.create(data=order_data)

        return JSONResponse(
            content={
                "order_id": razorpay_order["id"],
                "message": "Order created successfully. Please proceed with payment.",
            }
        )

    except Exception as e:
        logging.error(f"Error creating order: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error creating order: {str(e)}")


@app.post("/payment-callback")
async def payment_callback(request: Request):
    """Handle Razorpay payment webhook callback."""
    try:
        # Get Razorpay signature
        signature = request.headers.get("X-Razorpay-Signature")
        if not signature:
            raise HTTPException(status_code=400, detail="Missing signature")

        # Get raw JSON data
        body = await request.body()
        data = await request.json()

        # Extract payment_id
        payment_id = (
            data.get("payload", {})
            .get("payment", {})
            .get("entity", {})
            .get("id")
        )
        if not payment_id:
            raise HTTPException(status_code=400, detail="Missing payment_id")

        # Verify Signature
        expected_signature = hmac.new(
            bytes(RAZORPAY_WEBHOOK_SECRET, "utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()

        if expected_signature != signature:
            raise HTTPException(status_code=400, detail="Signature mismatch")

        return JSONResponse(
            content={"message": "Payment verification successful", "payment_id": payment_id},
            status_code=200,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Run the FastAPI app using Uvicorn
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000, reload=True)
