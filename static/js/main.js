// Main application JavaScript
document.addEventListener('DOMContentLoaded', () => {
    // DOM Elements
    const elements = {
        search: {
            searchInput: document.getElementById('search-input'),
            searchButton: document.getElementById('search-button'),
            advanced: {
                advSearchButton: document.getElementById('adv-search-button'),
                searchFiltersForm: document.getElementById('search-filters')
            }
        },
        selectAllCheckbox: document.getElementById('select-all-checkbox'),
        downloadSelectedButton: document.getElementById('download-selected-button'),
        resultsSectionAccordion: document.getElementById('results-section-accordion'),
        searchAccordion: document.getElementById('search-accordion'),
        resultsHeading: document.getElementById('results-heading'),
        resultsTable: document.getElementById('results-table'),
        resultsTableBody: document.querySelector('#results-table tbody'),
        searchLoading: document.getElementById('search-loading'),
        statusLoading: document.getElementById('status-loading'),
        statusTable: document.getElementById('status-table'),
        statusTableBody: document.querySelector('#status-table tbody'),
        modalOverlay: document.getElementById('modal-overlay'),
        detailsContainer: document.getElementById('details-container'),
        theme: {
            toggle: document.getElementById('theme-toggle'),
            text: document.getElementById('theme-text'),
            dropdown: document.querySelector('[uk-dropdown]'),
        },
        debug: {
            form: document.getElementById('debug-form'),
            button: document.getElementById('debug-button'),
            spinner: document.getElementById('debug-spinner')
        },
        restart: {
            form: document.getElementById('restart-form'),
            button: document.getElementById('restart-button'),
            spinner: document.getElementById('restart-spinner')
        }
    };

    // State
    let modalDetails = null;
    const selectedBooks = new Set();
    const STATE = {
        isSearching: false,
        isLoadingDetails: false,
    };

    // Constants
    const REFRESH_INTERVAL = 60000; // 60 seconds
    const API_ENDPOINTS = {
        search: '/request/api/search',
        info: '/request/api/info',
        download: '/request/api/download',
        status: '/request/api/status'
    };
    const FILTERS = ['isbn', 'author', 'title', 'lang' , 'sort', "content", "format"];

    // Utility Functions
    const utils = {
        debounce(func, wait) {
            let timeout;
            return function executedFunction(...args) {
                const later = () => {
                    clearTimeout(timeout);
                    func(...args);
                };
                clearTimeout(timeout);
                timeout = setTimeout(later, wait);
            };
        },

        showLoading(element) {
            element.removeAttribute('hidden');
        },

        hideLoading(element) {
            element.setAttribute('hidden', '');
        },

        showAccordion(element) {
            UIkit.accordion(element).toggle(1, true);
        },

        async fetchJson(url, options = {}) {
            try {
                const response = await fetch(url, options);
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
                return await response.json();
            } catch (error) {
                console.error('Fetch error:', error);
                throw error;
            }
        },

        createElement(tag, attributes = {}, children = []) {
            const element = document.createElement(tag);
            Object.entries(attributes).forEach(([key, value]) => {
                element[key] = value;
            });
            children.forEach(child => {
                if (typeof child === 'string') {
                    element.appendChild(document.createTextNode(child));
                } else {
                    element.appendChild(child);
                }
            });
            return element;
        },

        sortResultsTable(column, order = 'asc') {
            const rows = Array.from(elements.resultsTableBody.querySelectorAll('tr'));
            const headers = document.querySelectorAll('#results-table thead th');

            const parseFileSize = (size) => {
                const match = size.match(/([\d.]+)([KMGT]B)/i);
                if (!match) return 0;
                const [_, value, unit] = match;
                const multiplier = { KB: 1, MB: 1024, GB: 1024 * 1024, TB: 1024 * 1024 * 1024 };
                return parseFloat(value) * (multiplier[unit.toUpperCase()] || 1);
            };

            const parseTitle = (title) => {
                return title.replace(/^(The)\s+/i, '').trim();
            }

            const columnName = headers[column].textContent.trim().toLowerCase();

            const getCellValue = (row, column) => {
                const cell = row.querySelector(`td:nth-child(${column + 1})`);
                const text = cell ? cell.textContent.trim() : '';
                if (columnName === 'size') return parseFileSize(text);
                if (columnName === 'title') return parseTitle(text);
                return text;
            };

            rows.sort((a, b) => {
                const valA = getCellValue(a, column);
                const valB = getCellValue(b, column);

                if (!isNaN(valA) && !isNaN(valB)) {
                    return order === 'asc' ? valA - valB : valB - valA;
                }
                return order === 'asc'
                    ? valA.localeCompare(valB)
                    : valB.localeCompare(valA);
            });

            elements.resultsTableBody.innerHTML = '';
            rows.forEach(row => elements.resultsTableBody.appendChild(row));

            headers.forEach(header => {
                const icon = header.querySelector('.sort-icon');
                if (icon) {
                    icon.removeAttribute('uk-icon');
                }
            });

            const currentHeader = headers[column];
            const icon = currentHeader.querySelector('.sort-icon');
            if (icon) {
                icon.setAttribute('uk-icon', `icon: ${order === 'asc' ? 'triangle-up' : 'triangle-down'}`);
            }
        },

        updateDownloadSelectedButton() {
            elements.downloadSelectedButton.disabled = selectedBooks.size === 0;
        },

        handleCheckboxChange(event) {
            const checkbox = event.target;
            if (checkbox.checked) {
                selectedBooks.add(checkbox.value);
            } else {
                selectedBooks.delete(checkbox.value);
            }
            utils.updateDownloadSelectedButton();
        },

        async handleDownloadSelected() {
            if (selectedBooks.size === 0) return;

            const bookIds = Array.from(selectedBooks);
            const books = bookIds.map((bookId) => {
                const row = document.querySelector(`#book-${bookId}`).closest('tr');
                return {
                    title: row.querySelector('td:nth-child(4)').textContent,
                    author: row.querySelector('td:nth-child(5)').textContent
                };
            });

            // Confirmation modal
            const confirmationContent = `
                <h2>Confirm Download</h2>
                <p>Are you sure you want to download ${books.length} book${books.length > 1 ? 's' : ''}?</p>
                <div class="uk-overflow-auto">
                    <table class="uk-table uk-table-divider uk-table-small">
                        <thead>
                            <tr>
                                <th>Title</th>
                                <th>Author</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${books.map((book) => `
                                <tr>
                                    <td>${book.title}</td>
                                    <td>${book.author}</td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
                <div class="uk-flex uk-flex-between uk-margin-top">
                    <button id="cancel-download" class="uk-button uk-button-default">Cancel</button>
                    <button id="confirm-download" class="uk-button uk-button-primary">Download</button>
                </div>
            `;

            elements.detailsContainer.innerHTML = confirmationContent;

            document.getElementById('cancel-download').addEventListener('click', modal.close);
            document.getElementById('confirm-download').addEventListener('click', async () => {
                await utils.confirmDownload(bookIds);
            });

            modal.open();
        },

        async confirmDownload(bookIds) {
            bookIds.forEach((bookId) => {
                // Optimistically mark row as queued and disable button
                status.setRowStatus(bookId, 'queued');
                const btn = document.getElementById(`download-btn-${bookId}`);
                if (btn) btn.disabled = true;
                utils.fetchJson(`${API_ENDPOINTS.download}?id=${encodeURIComponent(bookId)}`)
                    .catch(() => {
                        // Re-enable on error
                        status.setRowStatus(bookId, 'error');
                        if (btn) btn.disabled = false;
                    });
            });

            this.clearAllCheckboxes();
            modal.close();
        },

        clearAllCheckboxes() {
            selectedBooks.forEach((bookId) => {
                const checkbox = document.getElementById(`book-${bookId}`);
                if (checkbox) checkbox.checked = false;
            });

            const selectAllCheckbox = document.getElementById('select-all-checkbox');
            if (selectAllCheckbox) selectAllCheckbox.checked = false;

            selectedBooks.clear();
            utils.updateDownloadSelectedButton();
        }
    };

    // Search Functions
    const search = {
        async performSearch(query) {
            utils.clearAllCheckboxes();
            if (STATE.isSearching) return;

            try {
                STATE.isSearching = true;
                utils.showLoading(elements.searchLoading);

                if (!elements.searchAccordion.classList.contains('uk-open')) {
                    utils.showAccordion(elements.resultsSectionAccordion);
                };
                
                const data = await utils.fetchJson(
                    `${API_ENDPOINTS.search}?${query}`
                );

                this.displayResults(data);
            } catch (error) {
                this.handleSearchError(error);
            } finally {
                STATE.isSearching = false;
                utils.hideLoading(elements.searchLoading);
            }
        },

        buildQuery() {
            let queryParams = [];

            if(elements.search.searchInput.value.trim()) {
                queryParams.push(`query=${encodeURIComponent(elements.search.searchInput.value.trim())}`);
            }

            if(elements.search.advanced.searchFiltersForm.hasAttribute('hidden')) {
                //Not advanced search
                return queryParams.join('&');
            }

            FILTERS.forEach(filterType => {
                if (filterType === 'format') {
                    // Handle format checkboxes
                    const checkboxes = document.querySelectorAll(`[id^="${filterType}-"]:checked`);
                    checkboxes.forEach(checkbox => {
                        queryParams.push(`${filterType}=${encodeURIComponent(checkbox.value)}`);
                    });
                } else {
                    const inputs = document.querySelectorAll(`[id^="${filterType}-input"]`);
                
                    inputs.forEach(input => {
                        const value = input.value.trim();
                        if (value) {  
                            queryParams.push(`${filterType}=${encodeURIComponent(value.trim())}`);
                        }
                    });
                }
            });

            return queryParams.join('&');
        },

        displayResults(books) {
            elements.resultsTableBody.innerHTML = '';

            if (!books.length) {
                this.displayNoResults();
                return;
            }

            books.forEach((book, index) => {
                const row = this.createBookRow(book, index);
                elements.resultsTableBody.appendChild(row);
            });
        },

        displayNoResults() {
            const row = utils.createElement('tr', {}, [
                utils.createElement('td', {
                    colSpan: '10',
                    textContent: 'No results found.'
                })
            ]);
            elements.resultsTableBody.appendChild(row);
        },

        createBookRow(book, index) {
            const checkboxCell = utils.createElement('td', {}, [
                utils.createElement('input', {
                    type: 'checkbox',
                    className: 'uk-checkbox',
                    id: 'book-' + book.id,
                    name: 'book-' + book.id,
                    value: book.id,
                    onchange: utils.handleCheckboxChange
                })
            ]);

            // Create a status cell with a stable id so we can update it later
            const statusCell = utils.createElement('td', {
                id: `status-${book.id}`,
                className: 'status-cell'
            });

            return utils.createElement('tr', { id: `row-${book.id}` }, [
                checkboxCell,
                this.createPreviewCell(book.preview),
                utils.createElement('td', { textContent: book.title || 'N/A' }),
                utils.createElement('td', { textContent: book.author || 'N/A' }),
                utils.createElement('td', { textContent: book.publisher || 'N/A' }),
                utils.createElement('td', { textContent: book.year || 'N/A' }),
                utils.createElement('td', { textContent: book.language || 'N/A' }),
                utils.createElement('td', { textContent: book.format || 'N/A' }),
                utils.createElement('td', { textContent: book.size || 'N/A' }),
                this.createActionCell(book),
                statusCell
            ]);
        },

        createPreviewCell(previewUrl) {
            if (!previewUrl) {
                return utils.createElement('td', { textContent: 'N/A' });
            }

            const img = utils.createElement('img', {
                src: previewUrl,
                alt: 'Book Preview',
                style: 'max-width: 60px;'
            });

            return utils.createElement('td', {}, [img]);
        },

        createActionCell(book) {
            const buttonDetails = utils.createElement('button', {
                className: 'uk-button uk-button-default uk-align-center uk-margin-small uk-width-1-1',
                onclick: () => bookDetails.show(book.id)
            }, [utils.createElement('span', { textContent: 'Details' })]);

            const downloadButton = utils.createElement('button', {
                className: 'uk-button uk-button-primary uk-align-center uk-margin-small uk-width-1-1',
                id: `download-btn-${book.id}`,
                onclick: async () => {
                    // Disable immediately and set queued status
                    downloadButton.disabled = true;
                    status.setRowStatus(book.id, 'queued');
                    try {
                        await utils.fetchJson(`${API_ENDPOINTS.download}?id=${encodeURIComponent(book.id)}`);
                        // Trigger a quick status refresh
                        status.fetch();
                    } catch (e) {
                        // Re-enable on error and show error status
                        downloadButton.disabled = false;
                        status.setRowStatus(book.id, 'error');
                    }
                }
            }, [utils.createElement('span', { textContent: 'Download' })]);

            return utils.createElement('td', {}, [buttonDetails, downloadButton]);
        },

        handleSearchError(error) {
            console.error('Search error:', error);
            elements.resultsTableBody.innerHTML = '';
            const errorRow = utils.createElement('tr', {}, [
                utils.createElement('td', {
                    colSpan: '10',
                    textContent: 'An error occurred while searching. Please try again.'
                })
            ]);
            elements.resultsTableBody.appendChild(errorRow);
        }
    };

    // Book Details Functions
    const bookDetails = {
        async show(bookId) {
            if (STATE.isLoadingDetails) return;
            
            try {
                STATE.isLoadingDetails = true;
                modal.open();
                elements.detailsContainer.innerHTML = '<p>Loading details...</p>';

                const book = await utils.fetchJson(
                    `${API_ENDPOINTS.info}?id=${encodeURIComponent(bookId)}`
                );

                modalDetails = book;
                this.displayDetails(book);
            } catch (error) {
                this.handleDetailsError(error);
            } finally {
                STATE.isLoadingDetails = false;
            }
        },

        displayDetails(book) {
            elements.detailsContainer.innerHTML = this.generateDetailsHTML(book);

            // Add event listeners
            document.getElementById('download-button')
                .addEventListener('click', () => this.downloadBook(book));
            document.getElementById('close-details')
                .addEventListener('click', modal.close);
        },

        generateDetailsHTML(book) {
            return `

                <div class="uk-card uk-card-default uk-child-width-1-2" uk-grid>
                    <div class="uk-card-media-left uk-cover-container uk-padding">
                        <img class="uk-height-medium" src="${book.preview || ''}" alt="Book Preview" uk-cover>
                        <canvas width="299" height="461"></canvas>
                    </div>
                    <div class="uk-card-body">
                        <h3>${book.title || 'No title available'}</h3>
                        <p><strong>Author:</strong> ${book.author || 'N/A'}</p>
                        <p><strong>Publisher:</strong> ${book.publisher || 'N/A'}</p>
                        <p><strong>Year:</strong> ${book.year || 'N/A'}</p>
                        <p><strong>Language:</strong> ${book.language || 'N/A'}</p>
                        <p><strong>Format:</strong> ${book.format || 'N/A'}</p>
                        <p><strong>Size:</strong> ${book.size || 'N/A'}</p>
                    </div>
                    
                    <button id="download-button" class="uk-button uk-button-primary" type="button">Download</button>
                    <button id="close-details" class="uk-button uk-button-default uk-modal-close" type="button">Close</button>
                </div>
                <ul uk-accordion>
                    <li>
                        <a class="uk-accordion-title" href>Further Information</a>
                        <div class="uk-accordion-content">
                            ${this.generateInfoList(book.info)}
                        </div>
                    </li>
                </ul>
            `;
        },

        generateInfoList(info) {
            if (!info) return '';

            const listItems = Object.entries(info)
                .map(([key, values]) => `
                    <li><strong>${key}:</strong> ${values.join(', ')}</li>
                `)
                .join('');

            return `<ul class="uk-list uk-list-bullet">${listItems}</ul>`;
        },

        async downloadBook(book) {
            if (!book) return;

            try {
                utils.showLoading(elements.searchLoading);
                await utils.fetchJson(
                    `${API_ENDPOINTS.download}?id=${encodeURIComponent(book.id)}`
                );

                modal.close();
                status.fetch();
            } catch (error) {
                console.error('Download error:', error);
            } finally {
                utils.hideLoading(elements.searchLoading);
            }
        },

        handleDetailsError(error) {
            console.error('Details error:', error);
            elements.detailsContainer.innerHTML = `
                <p>Error loading details. Please try again.</p>
                <div class="details-actions">
                    <button id="close-details" onclick="modal.close()">Close</button>
                </div>
            `;
            document.getElementById('close-details')
                .addEventListener('click', modal.close);
        }
    };

    // Status Functions
    const status = {
        // Map backend status names to UI label and classes
        mapStatus(s) {
            const map = {
                queued: { label: 'Queued', cls: 'uk-label uk-label-warning' },
                downloading: { label: 'Downloading', cls: 'uk-label uk-label-primary' },
                completed: { label: 'Completed', cls: 'uk-label uk-label-success' },
                failed: { label: 'Failed', cls: 'uk-label uk-label-danger' }
            };
            return map[s] || { label: s || '—', cls: 'uk-label' };
        },
        setRowStatus(bookId, s) {
            const cell = document.getElementById(`status-${bookId}`);
            if (!cell) return;
            const { label, cls } = this.mapStatus(s);
            cell.innerHTML = '';
            cell.appendChild(utils.createElement('span', { className: cls, textContent: label }));
            const btn = document.getElementById(`download-btn-${bookId}`);
            if (btn) {
                btn.disabled = ['queued', 'downloading'].includes(s);
                if (['failed'].includes(s)) btn.disabled = false;
            }
        },
        updateRows(data) {
            if (!data) return;
            // The backend status endpoint returns an object keyed by status -> { bookId: bookInfo }
            Object.entries(data).forEach(([statusName, books]) => {
                Object.entries(books || {}).forEach(([bookId, info]) => {
                    this.setRowStatus(bookId, statusName);
                });
            });
        },
        async fetch() {
            try {
                utils.showLoading(elements.statusLoading);
                const data = await utils.fetchJson(API_ENDPOINTS.status);
                this.display(data);
                this.updateRows(data);
            } catch (error) {
                this.handleError(error);
            } finally {
                utils.hideLoading(elements.statusLoading);
            }
        },

        display(data) {
            elements.statusTableBody.innerHTML = '';

            // Handle each status type
            Object.entries(data).forEach(([status, booksInStatus]) => {
                // If the status section has books
                if (Object.keys(booksInStatus).length > 0) {
                    // For each book in this status
                    Object.entries(booksInStatus).forEach(([bookId, bookData]) => {
                        this.addStatusRow(status, bookData);
                    });
                }
            });
        },

        addStatusRow(status, book) {
            if (!book.id || !book.title) return;

            const statusCell = utils.createElement('td', {
                className: `status-${status.toLowerCase()}`,
                textContent: status
            });

            let titleElement;
            if (book.download_path != null) {
                titleElement = utils.createElement('a', {
                    href: `/request/api/localdownload?id=${book.id}`,
                    target: '_blank',
                    textContent: book.title || 'N/A'
                });
            }
            else {
                titleElement = utils.createElement('td', { textContent: book.title || 'N/A' })
            }

            const row = utils.createElement('tr', {}, [
                statusCell,
                utils.createElement('td', { textContent: book.id }),
                titleElement,
                this.createPreviewCell(book.preview)
            ]);

            elements.statusTableBody.appendChild(row);
        },

        createPreviewCell(previewUrl) {
            const cell = utils.createElement('td');

            if (previewUrl) {
                const img = utils.createElement('img', {
                    src: previewUrl,
                    alt: 'Book Preview',
                    style: 'max-width: 60px; height: auto;'
                });
                cell.appendChild(img);
            } else {
                cell.textContent = 'N/A';
            }

            return cell;
        },

        handleError(error) {
            console.error('Status error:', error);
            elements.statusTableBody.innerHTML = '';

            const errorRow = utils.createElement('tr', {}, [
                utils.createElement('td', {
                    colSpan: '4',
                    className: 'error-message',
                    textContent: 'Error loading status. Will retry automatically.'
                })
            ]);

            elements.statusTableBody.appendChild(errorRow);
        }
    };

    // Modal Functions
    const modal = {
        open() {
            elements.modalOverlay.classList.add('active');
        },

        close() {
            elements.modalOverlay.classList.remove('active');
            modalDetails = null;
        }
    };

    // Theme Management
    const theme = {
        STORAGE_KEY: 'preferred-theme',
        
        init() {
            this.loadTheme();
            this.setupListeners();
        },

        loadTheme() {
            const savedTheme = localStorage.getItem(this.STORAGE_KEY) || 'auto';
            this.applyThemePreference(savedTheme);
            this.updateButtonText(savedTheme);
        },

        applyThemePreference(preference) {
            if (preference === 'auto') {
                const isDarkMode = window.matchMedia('(prefers-color-scheme: dark)').matches;
                document.documentElement.setAttribute('data-theme', isDarkMode ? 'dark' : 'light');
            } else {
                document.documentElement.setAttribute('data-theme', preference);
            }
        },

        setupListeners() {
            // Listen for system theme changes
            const darkModeMediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
            darkModeMediaQuery.addEventListener('change', (e) => {
                if (localStorage.getItem(this.STORAGE_KEY) === 'auto') {
                    document.documentElement.setAttribute('data-theme', e.matches ? 'dark' : 'light');
                }
            });
            
            // Theme selection listeners
            const themeLinks = document.querySelectorAll('.uk-dropdown-nav a[data-theme]');
            themeLinks.forEach(link => {
                link.addEventListener('click', (e) => {
                    e.preventDefault();
                    const selectedTheme = e.target.getAttribute('data-theme');
                    localStorage.setItem(this.STORAGE_KEY, selectedTheme);
                    this.applyThemePreference(selectedTheme);
                    this.updateButtonText(selectedTheme);
                });
            });
        },

        updateButtonText(currentTheme) {
            const themeText = currentTheme.charAt(0).toUpperCase() + currentTheme.slice(1);
            const themeTextElement = document.getElementById('theme-text');
            if (themeTextElement) {
                themeTextElement.textContent = themeText;
            }
        }
    };

    // Debug Functions
    const debug = {
        init() {
            if (!elements.debug.form) return;
            
            elements.debug.form.addEventListener('submit', (e) => {
                e.preventDefault(); // Prevent default form submission
                
                if (elements.debug.button.disabled) {
                    return; // Prevent multiple submissions
                }
                
                // Disable button and show spinner
                elements.debug.spinner.classList.remove('uk-hidden');
                elements.debug.button.disabled = true;
                
                // Use fetch to make the request
                fetch(elements.debug.form.action)
                    .then(response => {
                        if (!response.ok) {
                            throw new Error('Debug request failed');
                        }
                        // Store content disposition before returning blob
                        const contentDisposition = response.headers.get('Content-Disposition');
                        return response.blob().then(blob => {
                            return { blob, contentDisposition, url: response.url };
                        });
                    })
                    .then(data => {
                        // Create and click a download link
                        const url = URL.createObjectURL(data.blob);
                        const a = document.createElement('a');
                        a.style.display = 'none';
                        a.href = url;
                        
                        // Extract filename from Content-Disposition header, URL, or use default
                        let filename;
                        if (data.contentDisposition) {
                            const filenameRegex = /filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/;
                            const matches = filenameRegex.exec(data.contentDisposition);
                            if (matches && matches[1]) {
                                filename = matches[1].replace(/['"]/g, '');
                            }
                        }
                        
                        if (!filename) {
                            // Extract from URL if header is not available
                            const urlParts = data.url.split('/');
                            filename = urlParts[urlParts.length - 1];
                        }
                        
                        a.download = filename || "cwa-book-downloader-debug.zip";
                        document.body.appendChild(a);
                        a.click();
                        
                        // Clean up
                        setTimeout(() => {
                            URL.revokeObjectURL(url);
                            document.body.removeChild(a);
                            
                            // Only reset UI after download starts
                            elements.debug.spinner.classList.add('uk-hidden');
                            elements.debug.button.disabled = false;
                        }, 100);
                    })
                    .catch(error => {
                        console.error('Debug download error:', error);
                        
                        // Reset UI on error
                        elements.debug.spinner.classList.add('uk-hidden');
                        elements.debug.button.disabled = false;
                        
                        // Show error notification
                        UIkit.notification({
                            message: 'Error generating debug file. Please try again.',
                            status: 'danger',
                            pos: 'top-center',
                            timeout: 5000
                        });
                    });
            });
        }
    };

    // Restart Functions
    const restart = {
        init() {
            if (!elements.restart.form) return;
            
            elements.restart.form.addEventListener('submit', (e) => {
                e.preventDefault(); // Prevent default form submission
                
                if (elements.restart.button.disabled) {
                    return; // Prevent multiple submissions
                }
                
                // Show confirmation dialog
                UIkit.modal.confirm('Are you sure you want to restart the application? This will interrupt any ongoing downloads.').then(() => {
                    // User confirmed, proceed with restart
                    elements.restart.spinner.classList.remove('uk-hidden');
                    elements.restart.button.disabled = true;
                    
                    // Show restarting message
                    UIkit.notification({
                        message: 'Application restarting...',
                        status: 'warning',
                        pos: 'top-center',
                        timeout: 5000
                    });
                    
                    // Make the restart request - this will kill the server
                    fetch(elements.restart.form.action)
                        .catch(() => {
                            // Expected to fail when server shuts down
                        });
                    
                    // Wait a bit then refresh the page to reconnect to the restarted server
                    setTimeout(() => {
                        window.location.reload();
                    }, 3000);
                }, () => {
                    // User cancelled, do nothing
                });
            });
        }
    };

    // Event Listeners
    function setupEventListeners() {
        // Search events
        elements.search.searchButton.addEventListener('click', () => {
            const query = search.buildQuery();
            if(query) search.performSearch(query);
        });

        elements.search.advanced.advSearchButton.addEventListener('click', () => {
            const query = search.buildQuery();
            if(query) search.performSearch(query);
        });
        
        elements.search.searchInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();

                const query = search.buildQuery();
                if(query) search.performSearch(query);
            }
        });

        // Modal close on overlay click
        elements.modalOverlay.addEventListener('click', (e) => {
            if (e.target === elements.modalOverlay) {
                modal.close();
            }
        });

        // Keyboard accessibility
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && elements.modalOverlay.classList.contains('active')) {
                modal.close();
            }
        });
        // Download selected books
        elements.downloadSelectedButton.addEventListener('click', utils.handleDownloadSelected);

        // Check/uncheck all book checkboxes
        elements.selectAllCheckbox.addEventListener('change', (event) => {
            const isChecked = event.target.checked;

            document.querySelectorAll('.uk-checkbox').forEach((checkbox) => {
                if (checkbox !== elements.selectAllCheckbox) {
                    checkbox.checked = isChecked;
                    if (isChecked) {
                        selectedBooks.add(checkbox.value);
                    } else {
                        selectedBooks.delete(checkbox.value);
                    }
                }
            });
            utils.updateDownloadSelectedButton();
        });

        function setupSorting() {
            const headers = document.querySelectorAll('#results-table thead th[data-sort]');
            headers.forEach((header) => {
                let sortOrder = 'asc';
                header.addEventListener('click', () => {
                    const allHeaders = Array.from(document.querySelectorAll('#results-table thead th'));
                    const columnIndex = allHeaders.indexOf(header);
                    utils.sortResultsTable(columnIndex, sortOrder);
                    sortOrder = sortOrder === 'asc' ? 'desc' : 'asc';
                });
            });
        }

        setupSorting();

    }

    // Initialize
    function init() {
        setupEventListeners();
        theme.init();  // Initialize theme management
        debug.init();  // Initialize debug functionality
        restart.init(); // Initialize restart functionality
        status.fetch();
        setInterval(() => status.fetch(), REFRESH_INTERVAL);
    }

    init();
});