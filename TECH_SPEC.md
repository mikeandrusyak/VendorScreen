# VendorScreen AI - Technical Specification

## 1. Project Overview
VendorScreen AI is a Python-based integration backend for a Monday.com marketplace application. 
**Target Audience:** B2B sectors with high contractor turnover but without heavy ERPs (e.g., waste management IT, logistics, construction, distribution).
**Core Value:** Automates KYC/AML compliance checks for new vendors against sanction lists and PEP (Politically Exposed Persons) databases using the OpenSanctions API.

## 2. Tech Stack
- **Runtime:** Python 3.10+
- **Framework:** FastAPI (served by Uvicorn)
- **Integrations:** Monday.com GraphQL API, OpenSanctions API (via httpx)
- **Environment Management:** python-dotenv

## 3. Environment Variables (Pre-configured)
- `MONDAY_SIGNING_SECRET`: Used to verify webhook payloads.
- `OPENSANCTIONS_API_KEY`: Authentication for OpenSanctions.
- `MONDAY_BOARD_ID`, `COLUMN_ID_STATUS`, `COLUMN_ID_COUNTRY`, `COLUMN_ID_DETAILS`: Mapping IDs for Monday.com GraphQL mutations.

## 4. Architecture & Data Flow
1. **Trigger:** A new item (vendor) is created on the Monday.com board. Monday sends a POST request to our `/webhook` endpoint.
2. **Immediate Acknowledgment:** The server MUST immediately return a `200 OK` response to Monday.com to prevent webhook timeouts.
3. **Verification Process (Asynchronous):**
   - Extract the Item ID and Vendor Name from the webhook payload.
   - Send a GET request to the OpenSanctions API to check the Vendor Name.
4. **Update Process:**
   - Based on the OpenSanctions response, determine the risk level.
   - Execute a GraphQL mutation (`change_multiple_column_values`) against the Monday.com API to update the item.

## 5. Business Logic & Status Mapping
The API response from OpenSanctions must be mapped to the Monday.com Status column (`COLUMN_ID_STATUS`):
- **Clear (Green):** No matches found in OpenSanctions. 
- **Warning (Yellow):** Entity is flagged as a PEP (Politically Exposed Person) or has minor flags.
- **Critical (Red):** Direct match in active sanction lists.

The details of the findings (or links to the OpenSanctions profile) must be written to the `COLUMN_ID_DETAILS` text column.

## 6. AI Agent Coding Guidelines (CRITICAL)
- **Monday Challenge:** The `/webhook` endpoint MUST handle Monday's initial URL verification challenge. If `req.body.challenge` exists, return it as `{ "challenge": req.body.challenge }`.
- **Security:** Always verify the Monday webhook signature using `MONDAY_SIGNING_SECRET`.
- **Error Handling:** Wrap API calls in `try/catch`. Implement basic handling for `429 Too Many Requests` from OpenSanctions.
- **GraphQL Formatting:** Ensure column values in the Monday mutation are properly JSON-stringified as required by the Monday API.