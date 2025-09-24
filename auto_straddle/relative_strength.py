import requests
from bs4 import BeautifulSoup
import pandas as pd
from datetime import datetime, timedelta
from urllib.parse import quote
import time
import json
import os
from TelegramSend import telegram_send_api

# Can get from https://www.nseindia.com/market-data/live-market-indices also

def is_valid_index_name(text):
    text = text.strip()
    if (
        text.startswith("Nifty") or text.startswith("India VIX")
    ) and " " in text and len(text.split()) < 10:
        return True
    return False

def get_indices_from_page(url):
    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    session = requests.Session()
    resp = session.get(url, headers=headers, timeout=10)
    soup = BeautifulSoup(resp.text, "html.parser")

    index_names = set()

    # Search for list items and links that contain likely index names
    for tag in soup.find_all(['a', 'li']):
        text = tag.get_text(strip=True)
        if is_valid_index_name(text):
            index_names.add(text)

    return sorted(index_names)

def get_all_nse_indices():
    urls = {
        "broad": "https://www.nseindia.com/products-services//indices-broad-market",
        "sectoral": "https://www.nseindia.com/products-services/indices-sectoral",
        "thematic": "https://www.nseindia.com/products-services/indices-thematic",
        "strategy": "https://www.nseindia.com/products-services/indices-strategy",
    }

    all_indices = {}

    for cat_name, url in urls.items():
        try:
            print(f"Fetching {cat_name} indices...")
            index_list = get_indices_from_page(url)
            all_indices[cat_name] = index_list
        except (requests.RequestException, ValueError, KeyError, Exception) as e:
            print(f"Error fetching {cat_name} indices: {e}")
            all_indices[cat_name] = []

    return all_indices

def get_nse_session():
    """
    Create a session with NSE website to get cookies for API access
    
    Returns:
        requests.Session: Authenticated session
    """
    session = requests.Session()
    
    # Initial headers to visit the main page
    initial_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0"
    }
    
    try:
        print("Establishing NSE session...")
        
        # Step 1: Visit main NSE page to get initial cookies
        response = session.get("https://www.nseindia.com", headers=initial_headers, timeout=30)
        print(f"Main page response: {response.status_code}")
        
        # Step 2: Visit market data page to get additional cookies  
        market_headers = initial_headers.copy()
        market_headers.update({
            "Referer": "https://www.nseindia.com/",
            "Sec-Fetch-Site": "same-origin"
        })
        
        try:
            market_response = session.get("https://www.nseindia.com/market-data/live-equity-market", headers=market_headers, timeout=30)
            print(f"Market data page response: {market_response.status_code}")
        except:
            print("Market data page failed, continuing...")
        
        # Step 3: Visit indices page to get more context
        try:
            indices_response = session.get("https://www.nseindia.com/market-data/india-indices", headers=market_headers, timeout=30)
            print(f"Indices page response: {indices_response.status_code}")
        except:
            print("Indices page failed, continuing...")
            
        # Step 4: Try to access a simple API first to warm up session
        try:
            api_test_headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Referer": "https://www.nseindia.com/market-data/india-indices",
                "X-Requested-With": "XMLHttpRequest",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin"
            }
            
            # Test with a simple API call first
            test_response = session.get("https://www.nseindia.com/api/allIndices", headers=api_test_headers, timeout=30)
            print(f"API test response: {test_response.status_code}")
        except Exception as e:
            print(f"API test failed: {e}")
        
        # Step 5: Set up final headers for historical data API calls
        api_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Referer": "https://www.nseindia.com/market-data/india-indices",
            "X-Requested-With": "XMLHttpRequest",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache"
        }
        
        session.headers.update(api_headers)
        
        # Print cookies for debugging
        print(f"Session cookies: {len(session.cookies)} cookies obtained")
        for cookie in session.cookies:
            print(f"  - {cookie.name}: {cookie.value[:20]}...")
            
        return session
        
    except Exception as e:
        print(f"Error creating NSE session: {e}")
        return None


def get_historical_data(index_name, from_date, to_date, session=None, max_retries=3):
    """
    Fetch historical data for a given index from NSE API
    
    Args:
        index_name (str): Name of the index (e.g., "NIFTY 50", "NIFTY 100")
        from_date (str): Start date in DD-MM-YYYY format
        to_date (str): End date in DD-MM-YYYY format
        session (requests.Session): Authenticated session
        max_retries (int): Maximum number of retry attempts
    
    Returns:
        pd.DataFrame: Historical data with columns [Date, Open, High, Low, Close]
    """
    for attempt in range(max_retries):
        try:
            if session is None:
                session = get_nse_session()
                if session is None:
                    return None
            
            # URL encode the index name
            encoded_index = quote(index_name)
            url = f"https://www.nseindia.com/api/historical/indicesHistory?indexType={encoded_index}&from={from_date}&to={to_date}"
            
            print(f"Attempt {attempt + 1}: Fetching data for {index_name}  {url}")
            
            # Ensure we have the proper headers for this request
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Referer": "https://www.nseindia.com/market-data/india-indices",
                "X-Requested-With": "XMLHttpRequest",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache"
            }
            
            response = session.get(url, headers=headers, timeout=30)
            
            if response.status_code == 401:
                print(f"Authentication required for {index_name}. Creating new session...")
                session = get_nse_session()
                if session:
                    response = session.get(url, headers=headers, timeout=30)
                else:
                    continue
            
            if response.status_code == 403:
                print(f"Access forbidden for {index_name}. Waiting before retry...")
                time.sleep(2)
                continue
                
            if response.status_code == 429:
                print(f"Rate limited for {index_name}. Waiting before retry...")
                time.sleep(5)
                continue
            
            response.raise_for_status()
            
            # Handle potential compression issues
            try:
                data = response.json()
            except ValueError as json_error:
                print(f"JSON parsing error for {index_name}: {json_error}")
                print(f"Response content type: {response.headers.get('content-type')}")
                print(f"Response encoding: {response.headers.get('content-encoding')}")
                print(f"Raw response (first 100 chars): {response.content[:100]}")
                
                # Try to decode manually if it's compressed
                if response.headers.get('content-encoding') == 'br':
                    try:
                        import brotli
                        decompressed = brotli.decompress(response.content)
                        data = json.loads(decompressed.decode('utf-8'))
                        print(f"Successfully decompressed Brotli content for {index_name}")
                    except Exception as decomp_error:
                        print(f"Brotli decompression failed: {decomp_error}")
                        if attempt < max_retries - 1:
                            time.sleep(1)
                            continue
                        return None
                else:
                    if attempt < max_retries - 1:
                        time.sleep(1)
                        continue
                    return None
            
            if 'data' not in data or 'indexCloseOnlineRecords' not in data['data']:
                print(f"No data structure found for {index_name}")
                if attempt < max_retries - 1:
                    time.sleep(1)
                    continue
                return None
                
            records = data['data']['indexCloseOnlineRecords']
            
            if not records:
                print(f"Empty records for {index_name}")
                return None
            
            # Convert to DataFrame
            df = pd.DataFrame(records)
            
            # Rename columns for consistency
            df = df.rename(columns={
                'EOD_TIMESTAMP': 'Date',
                'EOD_OPEN_INDEX_VAL': 'Open',
                'EOD_HIGH_INDEX_VAL': 'High',
                'EOD_LOW_INDEX_VAL': 'Low',
                'EOD_CLOSE_INDEX_VAL': 'Close'
            })
            
            # Select only required columns
            df = df[['Date', 'Open', 'High', 'Low', 'Close']]
            
            # Convert Date to datetime
            df['Date'] = pd.to_datetime(df['Date'], format='%d-%b-%Y')
            
            # Sort by date (oldest first)
            df = df.sort_values('Date').reset_index(drop=True)
            
            print(f"✓ Successfully fetched {len(df)} records for {index_name}")
            return df
            
        except requests.exceptions.RequestException as e:
            print(f"Network error for {index_name} (attempt {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
        except (ValueError, KeyError) as e:
            print(f"Data parsing error for {index_name}: {e}")
            return None
        except Exception as e:
            print(f"Unexpected error for {index_name}: {e}")
            if attempt < max_retries - 1:
                time.sleep(1)
                continue
    
    print(f"✗ Failed to fetch data for {index_name} after {max_retries} attempts")
    return None


def calculate_relative_strength(index_data, benchmark_data, period_days):
    """
    Calculate relative strength of an index against benchmark for a specific period
    
    Args:
        index_data (pd.DataFrame): Historical data for the index
        benchmark_data (pd.DataFrame): Historical data for the benchmark (Nifty 50)
        period_days (int): Number of days to look back
    
    Returns:
        float: Relative strength percentage
    """
    try:
        if index_data is None or benchmark_data is None:
            return None
            
        if len(index_data) < period_days or len(benchmark_data) < period_days:
            print(f"Not enough data for {len(index_data)} or {len(benchmark_data)} for period {period_days}")
            return None
        
        # Get the most recent data points
        index_current = index_data['Close'].iloc[-1]
        index_past = index_data['Close'].iloc[-period_days]
        
        benchmark_current = benchmark_data['Close'].iloc[-1]
        benchmark_past = benchmark_data['Close'].iloc[-period_days]
        
        # Calculate returns
        index_return = ((index_current - index_past) / index_past) * 100
        benchmark_return = ((benchmark_current - benchmark_past) / benchmark_past) * 100
        
        # Calculate relative strength
        relative_strength = index_return - benchmark_return
        
        return round(relative_strength, 2)
        
    except (IndexError, ValueError, ZeroDivisionError, Exception) as e:
        print(f"Error calculating relative strength: {e}")
        return None


def get_api_date_range():
    """
    Get date range for API call (1 year of data)
    
    Returns:
        dict: Dictionary with from_date and to_date for API call
    """
    today = datetime.now()
    
    # Use dynamic dates - 1 year of data ending today
    today_date = today
    one_year_ago = today - timedelta(days=365)
    
    return {
        'from_date': one_year_ago.strftime('%d-%m-%Y'),
        'to_date': today_date.strftime('%d-%m-%Y')
    }


def get_period_days():
    """
    Get number of days for each relative strength period
    
    Returns:
        dict: Dictionary with period names and their day counts
    """
    return {
        '1W': 7,
        '1M': 30, 
        '6M': 180,
        '1Y': 200
    }


def clean_index_name_for_api(index_name):
    """
    Clean index name for NSE API call
    
    Args:
        index_name (str): Original index name from website
    
    Returns:
        str: Cleaned index name for API
    """
    # Remove "Index" from the end if present
    cleaned_name = index_name.replace(" Index", "").strip()
    
    # Convert to uppercase and handle special cases
    cleaned_name = cleaned_name.upper()
    
    # Handle specific transformations for NSE API format
    transformations = [
        # Remove extra spaces and normalize
        ("  ", " "),
        ("   ", " "),
        
        # Handle common patterns - keep existing spacing
        ("NIFTY50", "NIFTY50"),  # Keep as is
        ("NIFTY100", "NIFTY100"),  # Keep as is  
        ("NIFTY200", "NIFTY200"),  # Keep as is
        ("NIFTY500", "NIFTY500"),  # Keep as is
        
        # Fix spacing issues for midcap, smallcap, next indices
        ("NIFTY MIDCAP150", "NIFTY MIDCAP 150"),
        ("NIFTY MIDCAP250", "NIFTY MIDCAP 250"), 
        ("NIFTY SMALLCAP50", "NIFTY SMALLCAP 50"),
        ("NIFTY SMALLCAP100", "NIFTY SMALLCAP 100"),
        ("NIFTY SMALLCAP250", "NIFTY SMALLCAP 250"),
        ("NIFTY NEXT50", "NIFTY NEXT 50"),
        ("NIFTY MICROCAP250", "NIFTY MICROCAP 250"),
        
        # Handle specific index name formats
        ("NIFTY 50 INDEX", "NIFTY 50"),
        ("NIFTY 100 INDEX", "NIFTY 100"),
        ("NIFTY 200 INDEX", "NIFTY 200"),
        ("NIFTY 500 INDEX", "NIFTY 500"),
        
        # Handle sectoral indices
        ("NIFTY BANK INDEX", "NIFTY BANK"),
        ("NIFTY IT INDEX", "NIFTY IT"),
        ("NIFTY AUTO INDEX", "NIFTY AUTO"),
        ("NIFTY PHARMA INDEX", "NIFTY PHARMA"),
        ("NIFTY METAL INDEX", "NIFTY METAL"),
        ("NIFTY FMCG INDEX", "NIFTY FMCG"),
        ("NIFTY REALTY INDEX", "NIFTY REALTY"),
        ("NIFTY MEDIA INDEX", "NIFTY MEDIA"),
        ("NIFTY ENERGY INDEX", "NIFTY ENERGY"),
        ("NIFTY HEALTHCARE INDEX", "NIFTY HEALTHCARE"),
        ("NIFTY FINANCIAL SERVICES INDEX", "NIFTY FINANCIAL SERVICES"),
        ("NIFTY CONSUMER DURABLES INDEX", "NIFTY CONSUMER DURABLES"),
        ("NIFTY OIL AND GAS INDEX", "NIFTY OIL AND GAS"),
        ("NIFTY PSU BANK INDEX", "NIFTY PSU BANK"),
        ("NIFTY PRIVATE BANK INDEX", "NIFTY PRIVATE BANK"),
        ("NIFTY INFRASTRUCTURE INDEX", "NIFTY INFRASTRUCTURE"),
        ("NIFTY COMMODITIES INDEX", "NIFTY COMMODITIES"),
        ("NIFTY SERVICES SECTOR INDEX", "NIFTY SERVICES SECTOR"),
        ("NIFTY INDIA CONSUMPTION INDEX", "NIFTY INDIA CONSUMPTION"),
        ("NIFTY INDIA MANUFACTURING INDEX", "NIFTY INDIA MANUFACTURING")
    ]
    
    # Apply transformations
    for old, new in transformations:
        cleaned_name = cleaned_name.replace(old, new)
    
    # Final cleanup - remove extra spaces
    cleaned_name = " ".join(cleaned_name.split())
    
    return cleaned_name


def calculate_all_relative_strengths():
    """
    Calculate relative strength for all NSE indices against Nifty 50
    
    Returns:
        pd.DataFrame: DataFrame with relative strength data
    """
    print("Starting relative strength calculation for all NSE indices...")
    
    # Get all indices
    all_indices = get_all_nse_indices()
    
    # Flatten all indices into a single list
    indices_list = []
    for cat_data in all_indices.values():
        indices_list.extend(cat_data)
    
    # Add some common indices that might be missing
    common_indices = [
        "NIFTY 50", "NIFTY NEXT 50", "NIFTY 100", "NIFTY 200", "NIFTY 500",
        "NIFTY SMALLCAP 100", "NIFTY MIDCAP 100", "NIFTY BANK", "NIFTY IT",
        "NIFTY AUTO", "NIFTY PHARMA", "NIFTY FMCG", "NIFTY METAL",
        "NIFTY ENERGY", "NIFTY REALTY", "NIFTY MEDIA", "NIFTY PSU BANK"
    ]
    
    # Combine and remove duplicates
    indices_list = list(set(indices_list + common_indices))
    
    print(f"Total indices to process: {len(indices_list)}")
    
    # Get single date range for all API calls (1 year of data)
    api_dates = get_api_date_range()
    period_days = get_period_days()
    
    print(f"Using date range: {api_dates['from_date']} to {api_dates['to_date']}")
    
    # Create NSE session for API calls
    print("Creating NSE session...")
    nse_session = get_nse_session()
    
    if nse_session is None:
        print("Failed to create NSE session. Cannot proceed.")
        return None
    
    # Get Nifty 50 data as benchmark (1 year of data)
    print("Fetching Nifty 50 benchmark data...")
    nifty50_data = get_historical_data("NIFTY 50", api_dates['from_date'], api_dates['to_date'], nse_session)
    
    if nifty50_data is None:
        print("Failed to fetch Nifty 50 data. Cannot proceed.")
        return None
    
    results = []
    
    for i, index_name in enumerate(indices_list, 1):
        print(f"\nProcessing {i}/{len(indices_list)}: {index_name}")
        
        # Skip Nifty 50 itself (it's the benchmark, so RS = 0.0)
        if index_name == "NIFTY 50":
            result = {
                'Index': index_name,
                'RS_1W': 0.0,
                'RS_1M': 0.0,
                'RS_6M': 0.0,
                'RS_1Y': 0.0
            }
            results.append(result)
            continue
        
        # Clean index name for API call
        api_index_name = clean_index_name_for_api(index_name)
        print(f"API name: {api_index_name}")
        
        # Fetch index data (1 year of data)
        index_data = get_historical_data(api_index_name, api_dates['from_date'], api_dates['to_date'], nse_session)
        
        if index_data is None:
            print(f"Skipping {index_name} - data not available")
            continue
        
        # Calculate relative strength for all periods using the same data
        rs_1w = calculate_relative_strength(index_data, nifty50_data, period_days['1W'])
        rs_1m = calculate_relative_strength(index_data, nifty50_data, period_days['1M'])
        rs_6m = calculate_relative_strength(index_data, nifty50_data, period_days['6M'])
        rs_1y = calculate_relative_strength(index_data, nifty50_data, period_days['1Y'])
        
        result = {
            'Index': index_name,
            'RS_1W': rs_1w,
            'RS_1M': rs_1m,
            'RS_6M': rs_6m,
            'RS_1Y': rs_1y
        }
        
        results.append(result)
        
        # Add a small delay to avoid overwhelming the server
        time.sleep(0.5)
    
    # Convert to DataFrame
    df_results = pd.DataFrame(results)
    
    # Sort by 1Y relative strength (descending)
    df_results = df_results.sort_values('RS_1Y', ascending=False)
    
    return df_results


def save_results(df_results, filename=None, send_to_telegram=True, telegram_chat_id="-891000076"):
    """
    Save results to CSV and JSON files, and optionally send to Telegram
    
    Args:
        df_results (pd.DataFrame): Results DataFrame
        filename (str): Base filename (without extension)
        send_to_telegram (bool): Whether to send CSV to Telegram
        telegram_chat_id (str): Telegram chat ID to send to
    """
    if filename is None:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"nse_relative_strength_{timestamp}"
    
    # Save to CSV
    csv_filename = f"{filename}.csv"
    df_results.to_csv(csv_filename, index=False)
    print(f"Results saved to {csv_filename}")
    
    # Save to JSON
    json_filename = f"{filename}.json"
    df_results.to_json(json_filename, orient='records', indent=2)
    print(f"Results saved to {json_filename}")
    
    # Send to Telegram if requested
    if send_to_telegram:
        try:
            telegram_api = telegram_send_api()
            
            # Send summary message first
            total_indices = len(df_results)
            timestamp_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            message = f"""📊 *NSE Relative Strength Analysis*
            
🕒 Generated: {timestamp_str}
📈 Total Indices: {total_indices}

Top 5 Performers (1 Week):
"""
            
            # Add top 5 performers for 1 week
            top_5_1w = df_results.nlargest(5, 'RS_1W')[['Index', 'RS_1W']]
            for idx, row in top_5_1w.iterrows():
                message += f"• {row['Index']}: {row['RS_1W']:.2f}%\n"
            
            message += f"\n📎 Full results attached as CSV file."
            
            # Send message
            telegram_api.send_message(telegram_chat_id, message)
            print(f"✅ Summary message sent to Telegram chat {telegram_chat_id}")
            
            # Send CSV file
            telegram_api.send_file(telegram_chat_id, csv_filename)
            print(f"✅ CSV file sent to Telegram chat {telegram_chat_id}")
            
            # Clean up both CSV and JSON files after successful sending
            try:
                if os.path.exists(csv_filename):
                    os.remove(csv_filename)
                    print(f"🧹 CSV file {csv_filename} cleaned up after sending")
                if os.path.exists(json_filename):
                    os.remove(json_filename)
                    print(f"🧹 JSON file {json_filename} cleaned up after sending")
            except Exception as cleanup_error:
                print(f"⚠️ Warning: Could not clean up files: {cleanup_error}")
            
        except Exception as e:
            print(f"❌ Error sending to Telegram: {e}")
            print(f"📁 CSV file {csv_filename} retained due to Telegram send failure")
    
    return csv_filename, json_filename


def print_top_performers(df_results, top_n=10):
    """
    Print top performing indices for each time period
    
    Args:
        df_results (pd.DataFrame): Results DataFrame
        top_n (int): Number of top performers to show
    """
    print("\n" + "="*80)
    print("TOP PERFORMING INDICES (Relative to Nifty 50)")
    print("="*80)
    
    periods = ['RS_1W', 'RS_1M', 'RS_6M', 'RS_1Y']
    period_names = ['1 Week', '1 Month', '6 Months', '1 Year']
    
    for period, period_name in zip(periods, period_names):
        print(f"\n{period_name} Top {top_n}:")
        print("-" * 40)
        
        # Filter out None values and sort
        valid_data = df_results[df_results[period].notna()].copy()
        top_indices = valid_data.nlargest(top_n, period)
        
        for i, (_, row) in enumerate(top_indices.iterrows(), 1):
            rs_value = row[period]
            print(f"{i:2d}. {row['Index']:<30} {rs_value:>8.2f}%")


def test_few_indices():
    """
    Test the functionality with a few major indices first
    """
    print("Testing with a few major indices...")
    
    test_indices = ["NIFTY 50", "NIFTY BANK", "NIFTY IT", "NIFTY AUTO", "NIFTY PHARMA"]
    api_dates = get_api_date_range()
    
    print(f"Testing with date range: {api_dates['from_date']} to {api_dates['to_date']}")
    
    # Create session
    session = get_nse_session()
    if session is None:
        print("Failed to create NSE session")
        return False
    
    success_count = 0
    for index_name in test_indices:
        print(f"Testing {index_name}...")
        api_name = clean_index_name_for_api(index_name)
        print(f"  API name: {api_name}")
        data = get_historical_data(api_name, api_dates['from_date'], api_dates['to_date'], session)
        if data is not None:
            print(f"✓ Successfully fetched {len(data)} records for {index_name}")
            success_count += 1
        else:
            print(f"✗ Failed to fetch data for {index_name}")
        time.sleep(1)  # Small delay between requests
    
    print(f"\nTest Results: {success_count}/{len(test_indices)} indices successful")
    return success_count > 0




if __name__ == "__main__":
    print("NSE Indices Relative Strength Calculator")
    print("="*50)
    
    # First test with a few indices
    if not test_few_indices():
        print("NSE API access failed. Cannot proceed without real data.")
        print("Please check your internet connection and try again later.")
        exit(1)
    
    print("\nTest successful! Proceeding with full calculation...")
    print("="*50)
    
    # Calculate relative strengths
    results_df = calculate_all_relative_strengths()
    
    if results_df is not None:
        # Save results
        csv_file, json_file = save_results(results_df)
        
        # Print summary
        print("\nSUMMARY:")
        print(f"Total indices processed: {len(results_df)}")
        print(f"All calculations completed successfully")
        
        # Print top performers
        print_top_performers(results_df)
        
        print("\nDetailed results saved to:")
        print(f"- {csv_file}")
        print(f"- {json_file}")
    else:
        print("Failed to calculate relative strengths.")
    
    # Also run the original index listing for reference
    print("\n" + "="*50)
    print("AVAILABLE NSE INDICES BY CATEGORY")
    print("="*50)
    
    indices_by_category = get_all_nse_indices()
    
    for category, indices in indices_by_category.items():
        print(f"\n=== {category.capitalize()} Indices ({len(indices)}) ===")
        for idx in indices:
            print(" -", idx)
