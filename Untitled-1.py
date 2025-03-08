import requests
from requests.auth import HTTPBasicAuth

# Razorpay Credentials
RAZORPAY_KEY_ID = "rzp_test_Eq8uqfMCsJDdRE"
RAZORPAY_KEY_SECRET = "r4kGnV1xHkSm6Y8CgZdkIb4k"

# Razorpay Payment Link API URL
PAYMENT_LINK_URL = "https://api.razorpay.com/v1/payment_links"

# Payment link data (Fixed)
payment_link_data = {
    "amount": 50000,  # Amount in paise (₹500)
    "currency": "INR",
    "description": "Payment for Order",
    "customer": {
        "name": "John Doe",
        "email": "john.doe@example.com",
        "contact": "7504615396"
    },
    "reminder_enable": True,  # Corrected field
    "callback_url": "https://yourwebsite.com/payment-success",  # Replace with your actual success URL
    "callback_method": "get"
}

# Make API request to create payment link
response = requests.post(PAYMENT_LINK_URL, json=payment_link_data, auth=HTTPBasicAuth(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

# Print response
if response.status_code in [200, 201]:
    payment_link = response.json()
    print("✅ Payment Link Created:", payment_link["short_url"])  # Send this link to the user
else:
    print("❌ Payment Link Creation Failed:", response.json())
