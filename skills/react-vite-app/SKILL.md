# React + Vite Application Shell Skill

## Required files (all mandatory)
- index.html (project root — NOT inside src/)
- src/main.jsx
- src/App.jsx
- src/index.css
- package.json
- vite.config.js
- tailwind.config.js
- postcss.config.js

## index.html (must be in project root)
```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>App</title>
</head>
<body>
  <div id="root"></div>
  <script type="module" src="/src/main.jsx"></script>
</body>
</html>
```

## src/main.jsx
```jsx
import React from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import App from './App';

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
```

## src/App.jsx (routes injected dynamically per project)
```jsx
import { BrowserRouter, Routes, Route } from 'react-router-dom';
// import page components here

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* routes go here */}
      </Routes>
    </BrowserRouter>
  );
}
```

## src/index.css
```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```

## vite.config.js
```javascript
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
});
```

## tailwind.config.js
```javascript
export default {
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  theme: { extend: {} },
  plugins: [],
};
```

## postcss.config.js
```javascript
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
```

## Common mistakes — never do these
- index.html must be in project root not src/
- script src must be /src/main.jsx not ./main.jsx
- Always import React in main.jsx
- src/index.css must have the three @tailwind directives
- tailwind content must include .jsx files
