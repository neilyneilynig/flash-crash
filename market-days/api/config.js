export default function handler(_, res) {
  res.status(200).json({ apiBase: process.env.MARKET_DAYS_API_URL || "" });
}
