const { mondaySdk } = require('@mondaycom/apps-sdk');

const monday = mondaySdk();

// Maps our risk levels to Monday.com status column index values
const STATUS_INDEX = {
  Clear: 1,
  Warning: 2,
  Critical: 3,
};

async function updateVendorRecord(itemId, riskLevel, details) {
  const boardId = process.env.MONDAY_BOARD_ID;
  const columnValues = JSON.stringify({
    [process.env.COLUMN_ID_STATUS]: { index: STATUS_INDEX[riskLevel] },
    [process.env.COLUMN_ID_DETAILS]: { text: details },
  });

  const mutation = `
    mutation {
      change_multiple_column_values(
        board_id: ${boardId},
        item_id: ${itemId},
        column_values: ${JSON.stringify(columnValues)}
      ) {
        id
      }
    }
  `;

  const response = await monday.api(mutation);

  if (response.errors) {
    throw new Error(`Monday GraphQL error: ${JSON.stringify(response.errors)}`);
  }

  return response.data;
}

module.exports = { updateVendorRecord };
