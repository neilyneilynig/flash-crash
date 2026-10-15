# Market Days MVP

Market Days is separate from the party game at `/days`. It stores sessions and event logs in PostgreSQL, reads real
market reference quotes from Alpaca, and submits only **Alpaca paper** orders when an operator runs an AI cycle.

## Railway backend

Deploy this repository with the included `railway.toml` and attach a PostgreSQL service. Set these Railway variables:

```sh
DATABASE_URL=postgresql://...                 # supplied by Railway PostgreSQL
ALPACA_API_KEY=...
ALPACA_API_SECRET=...
ALPACA_PAPER_BASE_URL=https://paper-api.alpaca.markets
ALPACA_DATA_BASE_URL=https://data.alpaca.markets
MARKET_DAYS_ALLOWED_ORIGINS=https://your-vercel-app.vercel.app
```

Do not put these values in source control or the Vercel project. The backend creates `market_days_games` and
`market_days_events` on startup. Visit `https://<railway-domain>/days` for the backend-hosted MVP.

## Vercel frontend

Deploy the `market-days/` directory as a Vercel project. Set only:

```sh
MARKET_DAYS_API_URL=https://<railway-domain>
```

The Vercel UI calls the Railway API; the backend also emits Market Days updates on its existing `/ws` stream. Configure
the backend CORS origin above. The party game endpoints and `/play` remain unchanged.
