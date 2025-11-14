# Source Code Documentation

This directory contains the frontend application for Calibre-Web Automated Book Downloader.

## Structure

```
src/
└── frontend/          # React + TypeScript frontend application
    ├── public/        # Static assets (logo, favicon)
    ├── src/           # Source code
    │   ├── components/    # React components
    │   ├── App.tsx       # Main application component
    │   └── styles.css    # Global styles
    ├── package.json   # Dependencies and scripts
    ├── vite.config.ts # Vite configuration
    └── tsconfig.json  # TypeScript configuration
```

## Frontend Development

### Prerequisites
- Node.js (v16 or higher)
- npm or yarn

### Quick Start

From the project root:
```bash
# Install dependencies
make install

# Start development server (http://localhost:5173)
make dev

# Build for production
make build

# Preview production build
make preview

# Run type checking
make typecheck
```

Alternatively, from `src/frontend`:
```bash
npm install
npm run dev
npm run build
```

### Technology Stack
- **Framework**: React 18 with TypeScript
- **Build Tool**: Vite 5
- **Styling**: TailwindCSS 3
- **Communication**: WebSocket for real-time updates

### Key Features
- **Search Interface**: Real-time book search with filtering
- **Download Queue**: Live status updates via WebSocket
- **Details Modal**: Rich book information display
- **Responsive Design**: Mobile-first approach

## Development Tips

### Hot Module Replacement (HMR)
The development server supports HMR for instant feedback during development.

### API Integration
The frontend communicates with the Flask backend via:
- REST API endpoints (`/request/api/*`)
- WebSocket connection (`ws://localhost:8084/request/ws`)

### Building for Production
The production build is optimized and minified:
```bash
make build
```
Output is generated in `src/frontend/dist/`

### Type Safety
Run TypeScript checks without building:
```bash
make typecheck
```

## Debugging

### Development Server Issues
- Ensure port 5173 is available
- Check that the backend is running on port 8084
- Verify WebSocket connection in browser console

### Build Issues
- Clear `node_modules` and reinstall: `make clean && make install`
- Check Node.js version compatibility
- Verify TypeScript configuration

