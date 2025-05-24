import React, { useState, useEffect, useCallback } from 'react';

// Service Worker registration for PWA capabilities
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/service-worker.js')
      .then(registration => {
        console.log('SW registered: ', registration);
      })
      .catch(registrationError => {
        console.log('SW registration failed: ', registrationError);
      });
  });
}

function App() {
  const [backendUrl, setBackendUrl] = useState(localStorage.getItem('backendUrl') || '');
  const [message, setMessage] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isError, setIsError] = useState(false);

  // Save backend URL to localStorage whenever it changes
  useEffect(() => {
    localStorage.setItem('backendUrl', backendUrl);
  }, [backendUrl]);

  const callBackend = useCallback(async (endpoint) => {
    if (!backendUrl) {
      setMessage('Please set a backend URL.');
      setIsError(true);
      return;
    }
    setIsLoading(true);
    setIsError(false);
    setMessage(`Calling backend endpoint: ${endpoint}...`);

    try {
      const response = await fetch(`${backendUrl}/${endpoint}`);
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      const data = await response.json();
      setMessage(data.message || `Successfully called ${endpoint}.`);
      setIsError(false);
    } catch (error) {
      setMessage(`Error calling ${endpoint}: ${error.message}`);
      setIsError(true);
    } finally {
      setIsLoading(false);
    }
  }, [backendUrl]);

  const handleStartDownloader = () => callBackend('start_downloader');
  const handleCheckStatus = () => callBackend('check_status');

  return (
    <div className="min-h-screen bg-gray-100 flex flex-col items-center justify-center p-4">
      <div className="bg-white p-8 rounded-lg shadow-xl w-full max-w-md">
        <h1 className="text-3xl font-bold text-gray-800 mb-6 text-center">
          Calibre-Web Downloader PWA
        </h1>

        <div className="mb-6">
          <label htmlFor="backendUrl" className="block text-gray-700 text-sm font-bold mb-2">
            Backend API URL:
          </label>
          <input
            type="url"
            id="backendUrl"
            className="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline"
            placeholder="e.g., http://localhost:5000"
            value={backendUrl}
            onChange={(e) => setBackendUrl(e.target.value)}
          />
          <p className="text-xs text-gray-500 mt-1">
            This PWA requires a custom backend server to interact with the Python script.
          </p>
        </div>

        <div className="flex flex-col space-y-4">
          <button
            onClick={handleStartDownloader}
            disabled={isLoading || !backendUrl}
            className={`
              w-full py-3 px-4 rounded-md text-white font-semibold transition duration-300
              ${isLoading || !backendUrl ? 'bg-gray-400 cursor-not-allowed' : 'bg-blue-600 hover:bg-blue-700'}
            `}
          >
            {isLoading && message.startsWith('Calling backend endpoint: start_downloader') ? 'Starting...' : 'Start Downloader'}
          </button>

          <button
            onClick={handleCheckStatus}
            disabled={isLoading || !backendUrl}
            className={`
              w-full py-3 px-4 rounded-md text-blue-700 border-2 border-blue-600 font-semibold transition duration-300
              ${isLoading || !backendUrl ? 'bg-gray-200 text-gray-500 cursor-not-allowed' : 'hover:bg-blue-50'}
            `}
          >
            {isLoading && message.startsWith('Calling backend endpoint: check_status') ? 'Checking...' : 'Check Status'}
          </button>
        </div>

        {message && (
          <div
            className={`p-4 mt-6 rounded-md text-sm ${isError ? 'bg-red-100 text-red-800' : 'bg-green-100 text-green-800'}`}
            role="alert"
          >
            {message}
          </div>
        )}
      </div>

      <div className="mt-8 text-gray-600 text-center text-sm">
        <p>
          This PWA is a client-side interface. It requires a backend server (e.g., Flask/Node.js)
          to run the `calibre-web-automated-book-downloader` script and expose API endpoints.
        </p>
        <p className="mt-2">
          <a
            href="https://github.com/calibrain/calibre-web-automated-book-downloader"
            target="_blank"
            rel="noopener noreferrer"
            className="text-blue-600 hover:underline"
          >
            View Python Script on GitHub
          </a>
        </p>
      </div>
    </div>
  );
}

export default App;
