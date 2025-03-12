import os
import webbrowser
import http.server
import socketserver
import threading
import time
from dotenv import load_dotenv
import json

# Load environment variables
load_dotenv()

# Razorpay test credentials
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_API_KEY")

# Global variables to store payment response
payment_response = {}

# HTML content for the payment page
PAYMENT_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Razorpay Test Payment</title>
    <script src="https://checkout.razorpay.com/v1/checkout.js"></script>
</head>
<body>
    <h1>Making Test Payment...</h1>
    <script>
        var options = {
            "key": "%s", // Razorpay Key ID
            "order_id": "%s", // Order ID from input
            "handler": function (response) {
                fetch('/payment-callback', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        payment_id: response.razorpay_payment_id,
                        order_id: response.razorpay_order_id,
                        signature: response.razorpay_signature
                    })
                }).then(() => {
                    document.body.innerHTML = "<h1>Payment Completed. Check Terminal.</h1>";
                });
            }
        };
        var rzp = new Razorpay(options);
        rzp.open();
    </script>
</body>
</html>
"""

# HTTP handler to serve payment page and capture callback
class PaymentHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        self.order_id = kwargs.pop('order_id', None)  # Get order_id from kwargs
        super().__init__(*args, **kwargs)

    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            if not self.order_id:
                self.wfile.write(b"Error: No order_id provided")
                return
            html = PAYMENT_HTML % (RAZORPAY_KEY_ID, self.order_id)
            self.wfile.write(html.encode())
    
    def do_POST(self):
        if self.path == '/payment-callback':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            global payment_response
            payment_response = json.loads(post_data.decode())
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Callback received")

    def log_message(self, format, *args):
        pass  # Suppress default logging

def start_server(order_id, port=8001):
    """Start a local server to serve the payment page."""
    # Create a handler class with order_id
    Handler = lambda *args, **kwargs: PaymentHandler(*args, order_id=order_id, **kwargs)
    httpd = socketserver.TCPServer(("", port), Handler)
    
    server_thread = threading.Thread(target=httpd.serve_forever)
    server_thread.daemon = True
    server_thread.start()
    return httpd

def main():
    # Get order_id from user input
    order_id = input("Enter the Razorpay order_id: ").strip()
    if not order_id.startswith("order_"):
        print("Invalid order_id. It should start with 'order_'")
        return

    # Start the local server
    httpd = start_server(order_id)
    url = f"http://localhost:8001"

    # Open the payment page in the default browser
    print(f"Opening payment page at {url}...")
    webbrowser.open(url)

    # Wait for payment response
    print("Complete the payment in the browser. Waiting for response...")
    while not payment_response:
        time.sleep(1)  # Poll every second

    # Shutdown the server
    httpd.shutdown()
    httpd.server_close()

    # Print the payment details
    payment_id = payment_response.get("payment_id")
    signature = payment_response.get("signature")
    print("\nPayment Details:")
    print(f"Payment ID: {payment_id}")
    print(f"Razorpay Signature: {signature}")

    # You can now use these values with /payment/verify
    print("\nUse these values in your /payment/verify endpoint payload:")
    print(json.dumps({
        "payment_info": {
            "razorpay_order_id": order_id,
            "razorpay_payment_id": payment_id,
            "razorpay_signature": signature
        }
    }, indent=2))

if __name__ == "__main__":
    main()