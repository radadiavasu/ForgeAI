# Express.js Server Entry Point Skill

## Required files (all mandatory)
- src/server.js
- src/db.js
- src/routes/index.js
- package.json
- .env.example
- nodemon.json

## src/server.js structure
```javascript
import 'dotenv/config';
import express from 'express';
import cors from 'cors';
import helmet from 'helmet';
import { router } from './routes/index.js';

const app = express();
app.use(cors());
app.use(helmet());
app.use(express.json());
app.use('/api', router);

const PORT = process.env.PORT || 3001;
app.listen(PORT, () => console.log(`Server on ${PORT}`));
export { app };
```

## src/db.js structure
```javascript
import pg from 'pg';
const { Pool } = pg;
export const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
});
export const query = (text, params) => pool.query(text, params);
```

## package.json scripts
```json
{
  "scripts": {
    "start": "node src/server.js",
    "dev": "nodemon src/server.js",
    "test": "vitest"
  }
}
```

## Common mistakes — never do these
- Never hardcode DATABASE_URL or PORT
- Always export app for testing
- Always apply cors() before routes
- Always call express.json() middleware
- Never use require() — use ES module imports
