# Bill Data Extraction API

A FastAPI-based service for extracting structured bill data from invoice documents (PDFs and images) using OCR technology.

## Features

- Extracts line items from invoices and bills
- Supports multi-page PDF documents
- Handles both PDF and image formats
- Prevents double-counting of line items
- Returns structured JSON data

## API Endpoint

### POST `/extract-bill-data`

Extracts bill data from a document URL.

**Request Body:**
```json
{
  "document": "https://example.com/invoice.pdf"
}
```

**Response:**
```json
{
  "is_success": true,
  "data": {
    "pagewise_line_items": [
      {
        "page_no": "1",
        "bill_items": [
          {
            "item_name": "Livi 300mg Tab",
            "item_amount": 448,
            "item_rate": ""
          }
        ]
      }
    ]
  }
}
```

## Local Development

### Prerequisites

- Python 3.11+
- Tesseract OCR installed on your system
  - **Windows**: Download from [GitHub](https://github.com/UB-Mannheim/tesseract/wiki)
  - **macOS**: `brew install tesseract`
  - **Linux**: `sudo apt-get install tesseract-ocr`

### Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Run the server:
```bash
uvicorn main:app --reload
```

The API will be available at `http://localhost:8000`


## Notes

- The OCR accuracy depends on image quality
- For production use, consider using cloud OCR services (Google Vision API, Azure Cognitive Services) for better accuracy
- The extraction logic uses pattern matching and heuristics - you may need to fine-tune based on your invoice formats

