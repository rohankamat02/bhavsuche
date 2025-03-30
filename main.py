from flask import Flask, render_template
from kiteconnect import KiteConnect
import datetime
import os

app = Flask(__name__, template_folder='templates')

# Debug template folder
print("Current working directory:", os.getcwd())
print("Flask app root path:", app.root_path)
print("Templates folder path:", os.path.join(app.root_path, 'templates'))
print("Does templates folder exist?", os.path.exists(os.path.join(app.root_path, 'templates')))
print("Does index.html exist?", os.path.exists(os.path.join(app.root_path, 'templates', 'index.html')))

# Your Kite Connect credentials from MoneyGarage
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")

# Initialize Kite Connect
kite = KiteConnect(api_key=API_KEY)
kite.set_access_token(ACCESS_TOKEN)

# Function to get the current weekly expiry for Nifty and BankNifty
def get_current_expiry():
    today = datetime.date.today()
    # Assuming weekly expiry is on Thursday for Nifty/BankNifty
    days_to_thursday = (3 - today.weekday() + 7) % 7  # 3 is Thursday
    if days_to_thursday == 0 and today.weekday() > 3:  # If today is past Thursday
        days_to_thursday = 7
    expiry_date = today + datetime.timedelta(days=days_to_thursday)
    return expiry_date

# Function to fetch ATM option contracts for summary
def get_atm_option_contracts():
    # Fetch all instruments
    instruments = kite.instruments("NFO")

    # Get current expiry
    expiry_date = get_current_expiry()

    # Find Nifty and BankNifty ATM options
    nifty_call = None
    nifty_put = None
    banknifty_call = None
    banknifty_put = None

    # Get spot prices to determine ATM strikes
    try:
        indices = kite.quote(["NSE:NIFTY 50", "NSE:NIFTY BANK"])
        nifty_spot = indices["NSE:NIFTY 50"].get("last_price", 0)
        banknifty_spot = indices["NSE:NIFTY BANK"].get("last_price", 0)
    except Exception:
        nifty_spot = 0
        banknifty_spot = 0

    # Round to nearest 100 for Nifty, 100 for BankNifty
    nifty_strike = round(nifty_spot / 100) * 100 if nifty_spot else 0
    banknifty_strike = round(banknifty_spot / 100) * 100 if banknifty_spot else 0

    for instrument in instruments:
        if instrument["expiry"] == expiry_date:
            # Nifty Call and Put
            if instrument["name"] == "NIFTY" and instrument["strike"] == nifty_strike:
                if instrument["instrument_type"] == "CE":
                    nifty_call = instrument["tradingsymbol"]
                elif instrument["instrument_type"] == "PE":
                    nifty_put = instrument["tradingsymbol"]
            # BankNifty Call and Put
            if instrument["name"] == "BANKNIFTY" and instrument["strike"] == banknifty_strike:
                if instrument["instrument_type"] == "CE":
                    banknifty_call = instrument["tradingsymbol"]
                elif instrument["instrument_type"] == "PE":
                    banknifty_put = instrument["tradingsymbol"]

    return nifty_call, nifty_put, banknifty_call, banknifty_put

# Function to fetch option chain for Nifty and BankNifty
def get_option_chain():
    # Fetch all instruments for NFO (futures and options)
    instruments = kite.instruments("NFO")

    # Get current expiry
    expiry_date = get_current_expiry()

    # Define strike ranges
    nifty_strike_range = range(20000, 25001, 100)  # Nifty: 20000 to 25000, step 100
    banknifty_strike_range = range(50000, 55001, 100)  # BankNifty: 50000 to 55000, step 100

    # Collect option contracts
    nifty_options = {"calls": {}, "puts": {}}
    banknifty_options = {"calls": {}, "puts": {}}

    # Map strikes to trading symbols
    nifty_symbols = []
    banknifty_symbols = []

    for instrument in instruments:
        if instrument["expiry"] == expiry_date:
            # Nifty options
            if instrument["name"] == "NIFTY" and instrument["strike"] in nifty_strike_range:
                if instrument["instrument_type"] == "CE":
                    nifty_options["calls"][instrument["strike"]] = instrument["tradingsymbol"]
                    nifty_symbols.append(f"NFO:{instrument['tradingsymbol']}")
                elif instrument["instrument_type"] == "PE":
                    nifty_options["puts"][instrument["strike"]] = instrument["tradingsymbol"]
                    nifty_symbols.append(f"NFO:{instrument['tradingsymbol']}")
            # BankNifty options
            if instrument["name"] == "BANKNIFTY" and instrument["strike"] in banknifty_strike_range:
                if instrument["instrument_type"] == "CE":
                    banknifty_options["calls"][instrument["strike"]] = instrument["tradingsymbol"]
                    banknifty_symbols.append(f"NFO:{instrument['tradingsymbol']}")
                elif instrument["instrument_type"] == "PE":
                    banknifty_options["puts"][instrument["strike"]] = instrument["tradingsymbol"]
                    banknifty_symbols.append(f"NFO:{instrument['tradingsymbol']}")

    # Fetch quotes for all option contracts
    all_symbols = nifty_symbols + banknifty_symbols
    try:
        quotes = kite.quote(all_symbols) if all_symbols else {}
    except Exception as e:
        quotes = {}  # If the market is closed, quotes might fail

    # Process Nifty option chain
    nifty_chain = []
    for strike in nifty_strike_range:
        call_symbol = nifty_options["calls"].get(strike)
        put_symbol = nifty_options["puts"].get(strike)
        call_data = quotes.get(f"NFO:{call_symbol}", {})
        put_data = quotes.get(f"NFO:{put_symbol}", {})
        nifty_chain.append({
            "strike": strike,
            "call_oi": call_data.get("oi", "N/A"),
            "call_ltp": call_data.get("last_price", "N/A"),
            "call_change": call_data.get("ohlc", {}).get("close", 0) and round(((call_data.get("last_price", 0) - call_data.get("ohlc", {}).get("close", 0)) / call_data.get("ohlc", {}).get("close", 0)) * 100, 2) if call_data.get("ohlc", {}).get("close", 0) else "N/A",
            "put_oi": put_data.get("oi", "N/A"),
            "put_ltp": put_data.get("last_price", "N/A"),
            "put_change": put_data.get("ohlc", {}).get("close", 0) and round(((put_data.get("last_price", 0) - put_data.get("ohlc", {}).get("close", 0)) / put_data.get("ohlc", {}).get("close", 0)) * 100, 2) if put_data.get("ohlc", {}).get("close", 0) else "N/A"
        })

    # Process BankNifty option chain
    banknifty_chain = []
    for strike in banknifty_strike_range:
        call_symbol = banknifty_options["calls"].get(strike)
        put_symbol = banknifty_options["puts"].get(strike)
        call_data = quotes.get(f"NFO:{call_symbol}", {})
        put_data = quotes.get(f"NFO:{put_symbol}", {})
        banknifty_chain.append({
            "strike": strike,
            "call_oi": call_data.get("oi", "N/A"),
            "call_ltp": call_data.get("last_price", "N/A"),
            "call_change": call_data.get("ohlc", {}).get("close", 0) and round(((call_data.get("last_price", 0) - call_data.get("ohlc", {}).get("close", 0)) / call_data.get("ohlc", {}).get("close", 0)) * 100, 2) if call_data.get("ohlc", {}).get("close", 0) else "N/A",
            "put_oi": put_data.get("oi", "N/A"),
            "put_ltp": put_data.get("last_price", "N/A"),
            "put_change": put_data.get("ohlc", {}).get("close", 0) and round(((put_data.get("last_price", 0) - put_data.get("ohlc", {}).get("close", 0)) / put_data.get("ohlc", {}).get("close", 0)) * 100, 2) if put_data.get("ohlc", {}).get("close", 0) else "N/A"
        })

    return nifty_chain, banknifty_chain

# Function to fetch all required data
def get_indices_data():
    try:
        # Fetch Nifty, BankNifty, and India VIX data
        indices = kite.quote(["NSE:NIFTY 50", "NSE:NIFTY BANK", "NSE:INDIA VIX"])
        nifty = indices["NSE:NIFTY 50"]
        banknifty = indices["NSE:NIFTY BANK"]
        india_vix = indices["NSE:INDIA VIX"]

        # Fetch ATM OI data for Nifty and BankNifty
        nifty_call_symbol, nifty_put_symbol, banknifty_call_symbol, banknifty_put_symbol = get_atm_option_contracts()
        option_symbols = [f"NFO:{symbol}" for symbol in [nifty_call_symbol, nifty_put_symbol, banknifty_call_symbol, banknifty_put_symbol] if symbol]
        try:
            options_data = kite.quote(option_symbols) if option_symbols else {}
        except Exception:
            options_data = {}

        # Fetch option chain data
        nifty_chain, banknifty_chain = get_option_chain()

        return {
            "nifty": {
                "last_price": nifty.get("last_price", "N/A"),
                "timestamp": nifty.get("last_time", "Market Closed")
            },
            "banknifty": {
                "last_price": banknifty.get("last_price", "N/A"),
                "timestamp": banknifty.get("last_time", "Market Closed")
            },
            "india_vix": {
                "last_price": india_vix.get("last_price", "N/A"),
                "timestamp": india_vix.get("last_time", "Market Closed")
            },
            "options": {
                "nifty_call": options_data.get(f"NFO:{nifty_call_symbol}", {}).get("oi", "N/A"),
                "nifty_put": options_data.get(f"NFO:{nifty_put_symbol}", {}).get("oi", "N/A"),
                "banknifty_call": options_data.get(f"NFO:{banknifty_call_symbol}", {}).get("oi", "N/A"),
                "banknifty_put": options_data.get(f"NFO:{banknifty_put_symbol}", {}).get("oi", "N/A")
            },
            "nifty_chain": nifty_chain,
            "banknifty_chain": banknifty_chain
        }
    except Exception as e:
        return {"error": str(e)}

# Route for the webpage
@app.route('/')
def display_indices():
    data = get_indices_data()
    return render_template('index.html', data=data)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)