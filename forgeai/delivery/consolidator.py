"""Post-assembly consolidation: wire JS backend routes into a runnable app."""

from __future__ import annotations

import json
from pathlib import Path

from forgeai.llm.schemas import TechStackDocument


class ProjectConsolidator:
    """Consolidate backend task outputs into a runnable application layout."""

    def consolidate(self, output_dir: str, tech_stack: TechStackDocument) -> None:
        root = Path(output_dir)
        lang = tech_stack.language.lower()
        is_js = "javascript" in lang or "typescript" in lang
        if is_js:
            self._consolidate_js(root)

    def _consolidate_js(self, root: Path) -> None:
        api_dir = root / "src" / "api"
        if not api_dir.exists():
            return

        route_files = [
            f for f in api_dir.glob("*.js") if f.name != "client.js"
        ]
        if not route_files:
            return

        imports = [
            "const express = require('express');",
            "const router = express.Router();",
            "",
        ]

        for f in sorted(route_files):
            module_name = f.stem
            imports.append(
                f"const {module_name}Router = require('./{f.name}');"
            )

        imports.append("")

        for f in sorted(route_files):
            module_name = f.stem
            imports.append(f"router.use({module_name}Router);")

        imports.append("")
        imports.append("module.exports = router;")

        routes_dir = root / "src" / "routes"
        routes_dir.mkdir(exist_ok=True)
        (routes_dir / "index.js").write_text(
            "\n".join(imports), encoding="utf-8"
        )

        server_path = root / "src" / "server.js"
        if server_path.exists():
            server_content = server_path.read_text(encoding="utf-8")
            if "routes" not in server_content:
                routes_import = "const routes = require('./routes/index');\n"
                lines = server_content.split("\n")
                last_require = 0
                for i, line in enumerate(lines):
                    if "require(" in line:
                        last_require = i
                lines.insert(last_require + 1, routes_import)
                lines.insert(last_require + 2, "app.use('/api', routes);")
                server_path.write_text("\n".join(lines), encoding="utf-8")

        pkg_path = root / "package.json"
        if not pkg_path.exists() or pkg_path.stat().st_size < 50:
            pkg = {
                "name": "forgeai-project",
                "version": "1.0.0",
                "type": "commonjs",
                "scripts": {
                    "start": "node src/server.js",
                    "dev": "nodemon src/server.js",
                    "test": "jest",
                },
                "dependencies": {
                    "express": "^4.18.0",
                    "cors": "^2.8.5",
                    "helmet": "^7.0.0",
                    "dotenv": "^16.0.0",
                    "mongoose": "^8.0.0",
                    "jsonwebtoken": "^9.0.0",
                    "bcrypt": "^5.1.0",
                },
            }
            pkg_path.write_text(
                json.dumps(pkg, indent=2), encoding="utf-8"
            )
