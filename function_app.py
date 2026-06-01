import azure.functions as func
import logging
import json
import requests
import os
from datetime import datetime, timezone
from azure.storage.blob import BlobServiceClient

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

@app.route(route="getmarket", auth_level=func.AuthLevel.FUNCTION)
def getmarket(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Executing getmarket function.')

    api_key = os.environ.get("EXTERNAL_API_KEY")
    connection_string = os.environ.get("AzureWebJobsStorage")
    container_name = "marketstore1"

    if not api_key:
        logging.error("Key not found!")
        return func.HttpResponse("Error: EXTERNAL_API_KEY not found.", status_code=500)

    if not connection_string:
        logging.error("AzureWebJobsStorage not found!")
        return func.HttpResponse("Error: Storage connection string not found.", status_code=500)

    logging.info(f"API Key loaded successfully: {api_key[:4]}...")

    # --- Call the EOD endpoint for price data ---
    api_url = "https://api.marketstack.com/v1/eod"

    params = {
        "access_key": api_key,
        "symbols": req.params.get("symbols", "AAPL,MSFT,TSLA"),  # ?symbols=AAPL or default
        "limit":   req.params.get("limit", 100),
        "offset":  req.params.get("offset", 0)
    }

    try:
        response = requests.get(api_url, params=params, timeout=10)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logging.error(f"API request failed: {e}")
        return func.HttpResponse(f"Error calling external API: {str(e)}", status_code=502)

    # --- Parse the JSON response ---
    try:
        market_data = response.json()
    except ValueError as e:
        logging.error(f"Failed to parse JSON: {e}")
        return func.HttpResponse("Error: Invalid JSON from external API.", status_code=502)

    # --- Extract pagination and price data ---
    pagination = market_data.get("pagination", {})
    stocks = market_data.get("data", [])

    logging.info(f"Total records available: {pagination.get('total')}")
    logging.info(f"Records returned this call: {pagination.get('count')}")

    # --- Parse each day's price fields ---
    results = []
    for day in stocks:
        date        = day.get("date", "")[:10]   # "2024-01-15T00:00:00+0000" → "2024-01-15"
        open_price  = day.get("open")
        high_price  = day.get("high")
        low_price   = day.get("low")
        close_price = day.get("close")
        volume      = day.get("volume")
        symbol      = day.get("symbol")
        exchange    = day.get("exchange")

        results.append({
            "symbol":   symbol,
            "date":     date,
            "open":     open_price,
            "high":     high_price,
            "low":      low_price,
            "close":    close_price,
            "volume":   volume,
            "exchange": exchange
        })

    final_output = {
        "pagination": pagination,
        "data": results
    }

    # --- Save to Azure Blob Storage ---
    try:
        blob_service_client = BlobServiceClient.from_connection_string(connection_string)

        container_client = blob_service_client.get_container_client(container_name)
        if not container_client.exists():
            container_client.create_container()
            logging.info(f"Container '{container_name}' created.")

        timestamp  = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        symbols    = params["symbols"].replace(",", "_")
        blob_name  = f"eod/{symbols}_{timestamp}.json"

        blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)
        blob_client.upload_blob(
            json.dumps(final_output, indent=2),
            overwrite=True
        )

        logging.info(f"Data saved to blob: {blob_name}")

    except Exception as e:
        logging.error(f"Blob storage upload failed: {e}")
        return func.HttpResponse(
            body=json.dumps({"warning": "Data fetched but blob upload failed", "error": str(e), **final_output}),
            status_code=207,
            mimetype="application/json"
        )

    # --- Return JSON response ---
    return func.HttpResponse(
        body=json.dumps(final_output),
        status_code=200,
        mimetype="application/json"
    )