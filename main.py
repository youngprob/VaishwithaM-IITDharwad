from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl
import requests
import io
import os
from typing import List, Optional
import re
from PIL import Image
import pytesseract
import pdf2image
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Bill Data Extraction API", version="1.0.0")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic models
class DocumentRequest(BaseModel):
    document: str

class BillItem(BaseModel):
    item_name: str
    item_amount: float
    item_rate: float = 0.0
    item_quantity: float = 0.0

class PagewiseLineItems(BaseModel):
    page_no: str
    page_type: str = ""
    bill_items: List[BillItem]

class TokenUsage(BaseModel):
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

class BillData(BaseModel):
    pagewise_line_items: List[PagewiseLineItems]
    total_item_count: int = 0

class APIResponse(BaseModel):
    is_success: bool
    token_usage: TokenUsage = TokenUsage()
    data: Optional[BillData] = None

def download_document(url: str) -> bytes:
    """Download document from URL"""
    try:
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()
        return response.content
    except requests.exceptions.RequestException as e:
        logger.error(f"Error downloading document: {e}")
        raise HTTPException(status_code=400, detail=f"Could not retrieve document: {str(e)}")

def find_tesseract():
    """Find Tesseract executable path"""
    import shutil
    
    # First check if it's in PATH
    tesseract_path = shutil.which("tesseract")
    if tesseract_path:
        return tesseract_path
    
    # Check common installation paths
    common_paths = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    
    for path in common_paths:
        if os.path.exists(path):
            return path
    
    return None

def extract_text_from_image(image: Image.Image) -> str:
    """Extract text from image using OCR"""
    try:
        # Try to find and set Tesseract path
        tesseract_path = find_tesseract()
        if tesseract_path:
            pytesseract.pytesseract.tesseract_cmd = tesseract_path
            logger.info(f"Using Tesseract at: {tesseract_path}")
        
        # Configure Tesseract for better accuracy
        custom_config = r'--oem 3 --psm 6'
        text = pytesseract.image_to_string(image, config=custom_config)
        return text
    except Exception as e:
        logger.error(f"OCR error: {e}")
        error_msg = str(e)
        if "tesseract" in error_msg.lower() or "not found" in error_msg.lower():
            raise HTTPException(
                status_code=500, 
                detail="OCR processing failed: Tesseract not found. Please install Tesseract OCR and ensure it's in PATH or at C:\\Program Files\\Tesseract-OCR\\tesseract.exe"
            )
        raise HTTPException(status_code=500, detail=f"OCR processing failed: {str(e)}")

def process_pdf(content: bytes) -> List[Image.Image]:
    """Convert PDF to images"""
    try:
        # Try to use poppler from common Windows installation paths
        poppler_path = None
        import os
        
        # Check common poppler paths on Windows
        common_paths = [
            r"C:\poppler-25.11.0\Library\bin",  # User's installation
            r"C:\poppler\Library\bin",
            r"C:\Program Files\poppler\Library\bin",
            r"C:\Program Files (x86)\poppler\Library\bin",
            os.path.join(os.environ.get('LOCALAPPDATA', ''), 'poppler', 'Library', 'bin'),
        ]
        
        # Also check for poppler in any folder starting with "poppler" in C:\
        if os.path.exists("C:\\"):
            try:
                for item in os.listdir("C:\\"):
                    if item.startswith("poppler") and os.path.isdir(os.path.join("C:\\", item)):
                        potential_path = os.path.join("C:\\", item, "Library", "bin")
                        if potential_path not in common_paths:
                            common_paths.insert(0, potential_path)  # Add to front for priority
            except PermissionError:
                pass  # Skip if no permission
        
        for path in common_paths:
            if os.path.exists(path) and os.path.exists(os.path.join(path, 'pdftoppm.exe')):
                poppler_path = path
                logger.info(f"Found poppler at: {poppler_path}")
                break
        
        # Use poppler_path if found, otherwise let pdf2image use PATH
        if poppler_path:
            images = pdf2image.convert_from_bytes(content, dpi=300, poppler_path=poppler_path)
        else:
            images = pdf2image.convert_from_bytes(content, dpi=300)
        return images
    except Exception as e:
        logger.error(f"PDF processing error: {e}")
        error_msg = str(e)
        if "poppler" in error_msg.lower() or "pdftoppm" in error_msg.lower():
            raise HTTPException(
                status_code=500, 
                detail="PDF processing failed: Poppler is not installed. Please install Poppler and add it to PATH, or extract it to C:\\poppler\\Library\\bin. See INSTALL_POPPLER.md for instructions."
            )
        raise HTTPException(status_code=500, detail=f"PDF processing failed: {str(e)}")

def process_image(content: bytes) -> Image.Image:
    """Process image from bytes"""
    try:
        image = Image.open(io.BytesIO(content))
        return image
    except Exception as e:
        logger.error(f"Image processing error: {e}")
        raise HTTPException(status_code=500, detail=f"Image processing failed: {str(e)}")

def detect_page_type(text: str) -> str:
    """Detect the type of page (Bill Detail, Final Bill, Pharmacy, etc.)"""
    text_lower = text.lower()
    
    # Check for pharmacy-related keywords
    if any(word in text_lower for word in ['pharmacy', 'medicine', 'tablet', 'capsule', 'syrup', 'injection']):
        return "Pharmacy"
    
    # Check for final bill keywords
    if any(word in text_lower for word in ['final bill', 'total payable', 'grand total', 'amount payable']):
        return "Final Bill"
    
    # Check for bill detail keywords
    if any(word in text_lower for word in ['bill detail', 'item details', 'particulars', 'description']):
        return "Bill Detail"
    
    # Default to Bill Detail if no specific type detected
    return "Bill Detail"

def extract_line_items_from_text(text: str) -> List[dict]:
    """Extract line items from OCR text using pattern matching"""
    items = []
    lines = text.split('\n')
    
    # Patterns to identify line items
    amount_pattern = r'[\d,]+\.?\d*'
    
    # Keywords that indicate summary/total lines (not items)
    skip_patterns = [
        r'total', r'subtotal', r'tax', r'gst', r'vat', r'discount', 
        r'amount', r'payable', r'invoice', r'bill', r'date', r'page',
        r'no\s*\d+', r'number', r'sum', r'grand',
        r'balance', r'paid', r'due', r'rupees', r'rs\.', r'₹'
    ]
    
    # Compile skip patterns for efficiency
    skip_regex = re.compile('|'.join(skip_patterns), re.IGNORECASE)
    
    for line in lines:
        line = line.strip()
        if not line or len(line) < 3:
            continue
        
        # Skip lines that match summary patterns
        if skip_regex.search(line):
            continue
        
        # Check if line contains numbers (likely amounts)
        amounts = re.findall(amount_pattern, line.replace(',', ''))
        
        if len(amounts) == 0:
            continue
        
        # Extract numeric values
        numeric_values = []
        for amt in amounts:
            try:
                # Remove commas and convert
                clean_amt = amt.replace(',', '')
                val = float(clean_amt)
                # Reasonable range for item amounts (0.01 to 999,999)
                if 0.01 <= val < 1000000:
                    numeric_values.append(val)
            except (ValueError, AttributeError):
                continue
        
        if not numeric_values:
            continue
        
        # The largest number is likely the item amount
        item_amount = max(numeric_values)
        
        # Extract item name by removing numeric values and special characters
        item_name = line
        # Remove all numeric patterns
        for amt in amounts:
            item_name = item_name.replace(amt, ' ')
        
        # Clean up item name - keep alphanumeric, spaces, hyphens, parentheses
        item_name = re.sub(r'[^\w\s\-\(\)\/]', ' ', item_name)
        item_name = re.sub(r'\s+', ' ', item_name).strip()
        
        # Validate item name
        if not item_name or len(item_name) < 2:
            continue
        
        # Skip if item name is just numbers or very short
        if item_name.isdigit() or len(item_name) < 2:
            continue
        
        # Extract rate and quantity from numeric values
        item_rate = 0.0
        item_quantity = 0.0
        
        if len(numeric_values) > 1:
            # Sort values - typically: quantity, rate, amount (in order of appearance or size)
            sorted_values = sorted(numeric_values, reverse=True)
            
            # The largest is the amount (already set)
            # Try to identify rate and quantity
            # Rate is usually a medium value, quantity is usually a small integer
            remaining_values = [v for v in sorted_values if v != item_amount]
            
            if len(remaining_values) >= 2:
                # Second largest might be rate
                item_rate = remaining_values[0]
                # Smallest might be quantity (if it's a reasonable quantity like 1-100)
                if remaining_values[-1] <= 100 and remaining_values[-1].is_integer():
                    item_quantity = remaining_values[-1]
                else:
                    item_quantity = remaining_values[-1]
            elif len(remaining_values) == 1:
                # Only one other value - could be rate or quantity
                val = remaining_values[0]
                if val <= 100 and val.is_integer():
                    item_quantity = val
                else:
                    item_rate = val
        
        items.append({
            "item_name": item_name,
            "item_amount": float(item_amount),
            "item_rate": float(item_rate),
            "item_quantity": float(item_quantity)
        })
    
    # Remove duplicates based on item_name and item_amount (with tolerance)
    seen = set()
    unique_items = []
    for item in items:
        # Normalize item name for comparison
        normalized_name = re.sub(r'\s+', ' ', item["item_name"].lower().strip())
        # Use rounded amount for duplicate detection (to handle OCR variations)
        rounded_amount = round(item["item_amount"], 2)
        key = (normalized_name, rounded_amount)
        
        if key not in seen:
            seen.add(key)
            unique_items.append(item)
    
    return unique_items

def process_document_with_ocr(document_url: str) -> dict:
    """Main function to process document and extract bill data"""
    try:
        # Download document
        content = download_document(document_url)
        
        # Determine document type and process
        pagewise_items = []
        total_item_count = 0
        
        # Try PDF first
        if document_url.lower().endswith('.pdf') or content[:4] == b'%PDF':
            images = process_pdf(content)
            for idx, image in enumerate(images, 1):
                text = extract_text_from_image(image)
                page_type = detect_page_type(text)
                items = extract_line_items_from_text(text)
                total_item_count += len(items)
                
                pagewise_items.append({
                    "page_no": str(idx),
                    "page_type": page_type,
                    "bill_items": items
                })
        else:
            # Process as image
            image = process_image(content)
            text = extract_text_from_image(image)
            page_type = detect_page_type(text)
            items = extract_line_items_from_text(text)
            total_item_count = len(items)
            
            pagewise_items.append({
                "page_no": "1",
                "page_type": page_type,
                "bill_items": items
            })
        
        if not pagewise_items:
            # Return empty structure if no items found
            return {
                "pagewise_line_items": [{
                    "page_no": "1",
                    "page_type": "Bill Detail",
                    "bill_items": []
                }],
                "total_item_count": 0
            }
        
        return {
            "pagewise_line_items": pagewise_items,
            "total_item_count": total_item_count
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Document processing error: {e}")
        raise HTTPException(status_code=500, detail=f"Document processing failed: {str(e)}")

@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "active",
        "message": "Bill Data Extraction API is running",
        "endpoint": "/extract-bill-data"
    }

@app.post("/extract-bill-data", response_model=APIResponse)
async def extract_bill_data(request: DocumentRequest):
    """
    Extract bill data from a document URL.
    
    Accepts a document URL (PDF or image) and returns structured bill data
    with line items organized by page.
    """
    try:
        if not request.document:
            raise HTTPException(status_code=400, detail="Document URL is required")
        
        logger.info(f"Processing document: {request.document}")
        extracted_data = process_document_with_ocr(request.document)
        
        # Create token usage (set to 0 since we're not using LLM)
        token_usage = TokenUsage(
            total_tokens=0,
            input_tokens=0,
            output_tokens=0
        )
        
        return APIResponse(
            is_success=True,
            token_usage=token_usage,
            data=BillData(**extracted_data)
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

