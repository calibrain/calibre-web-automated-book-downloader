use crate::config::CONFIG;
use anyhow::{anyhow, Result};
use reqwest;
use reqwest::Client;
use std::time::Duration;
use tokio;
use wiremock::matchers::{header, method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

static APP_USER_AGENT: &str = concat!(
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ",
    "AppleWebKit/537.36 (KHTML, like Gecko) ",
    "Chrome/129.0.0.0 Safari/537.3"
);

/// Fetches HTML from a given URL, retrying on error up to `CONFIG.max_retry` times.
///
/// Returns the response body if successful, or an `anyhow::Error` if:
/// - The request fails to send,
/// - The server returns an unsuccessful status,
/// - The response body cannot be read,
/// - or all retries are exhausted.
pub async fn html_get_page(url: String) -> Result<String> {
    let client = Client::new();
    println!("GET {}", url);

    for attempt in 0..CONFIG.max_retry {
        println!("Attempt {}", attempt + 1);

        // Try sending the request
        let response = match client
            .get(&url)
            .header("User-Agent", APP_USER_AGENT)
            .send()
            .await
        {
            Ok(resp) => resp,
            Err(e) => {
                // Sending the request failed (network error, DNS error, etc.)
                if attempt + 1 >= CONFIG.max_retry {
                    return Err(anyhow!(
                        "Network error after {} attempts: {}",
                        attempt + 1,
                        e
                    ));
                } else {
                    let delay = Duration::from_secs(CONFIG.retry_wait_duration);
                    println!(
                        "Network error: {}. Retrying in {}s...",
                        e, CONFIG.retry_wait_duration
                    );
                    tokio::time::sleep(delay).await;
                    continue;
                }
            }
        };

        // Check if status code is 2xx
        if !response.status().is_success() {
            if attempt + 1 >= CONFIG.max_retry {
                return Err(anyhow!(
                    "Server returned non-success status {} after {} attempts",
                    response.status(),
                    attempt + 1
                ));
            } else {
                let delay = Duration::from_secs(CONFIG.retry_wait_duration);
                println!(
                    "HTTP status error: {}. Retrying in {}s...",
                    response.status(),
                    CONFIG.retry_wait_duration
                );
                tokio::time::sleep(delay).await;
                continue;
            }
        }

        // We have a 2xx status, so let's read the body
        match response.text().await {
            Ok(body) => {
                println!("Success!");
                return Ok(body);
            }
            Err(e) => {
                if attempt + 1 >= CONFIG.max_retry {
                    return Err(anyhow!(
                        "Failed to read response body after {} attempts: {}",
                        attempt + 1,
                        e
                    ));
                }
                let delay = Duration::from_secs(CONFIG.retry_wait_duration);
                println!(
                    "Error reading body: {}. Retrying in {}s...",
                    e, CONFIG.retry_wait_duration
                );
                tokio::time::sleep(delay).await;
            }
        }
    }

    // If we exit the loop, we've exhausted all retries
    Err(anyhow!(
        "Exhausted all retries ({} attempts) for URL: {}",
        CONFIG.max_retry,
        url
    ))
}

/* TESTS */

#[tokio::test]
async fn test_html_get_page_success() {
    // Start a mock server
    let mock_server = MockServer::start().await;

    // Define the expected response body
    let expected_body = "<html><body>Success</body></html>";

    // Configure the mock to respond with 200 OK and the expected body
    Mock::given(method("GET"))
        .and(path("/success"))
        .respond_with(ResponseTemplate::new(200).set_body_string(expected_body))
        .mount(&mock_server)
        .await;

    // Construct the URL for the mock endpoint
    let url = format!("{}/success", &mock_server.uri());

    // Call the `html_get_page` function
    let result = html_get_page(url).await;

    // Assert that the result matches the expected body
    assert_eq!(result.unwrap(), expected_body);
}

#[tokio::test]
async fn test_html_get_page_retry_exhausted() {
    // Start a mock server
    let mock_server = MockServer::start().await;

    // Configure the mock to always respond with 500 Internal Server Error
    Mock::given(method("GET"))
        .and(path("/always_fail"))
        .respond_with(ResponseTemplate::new(500))
        // We expect exactly `CONFIG.max_retry` attempts
        .expect(CONFIG.max_retry)
        .mount(&mock_server)
        .await;

    // Construct the URL for the mock endpoint
    let url = format!("{}/always_fail", &mock_server.uri());

    // Call the `html_get_page` function
    let result = html_get_page(url).await;

    // Assert that the result is an error
    assert!(
        result.is_err(),
        "Expected an error after exhausting retries"
    );
}

#[tokio::test]
async fn test_html_get_page_invalid_url() {
    // Define an invalid URL
    let invalid_url = "http://".to_string(); // <-- deliberately broken

    // Call the `html_get_page` function
    let result = html_get_page(invalid_url).await;

    // Assert that the result is an error
    assert!(result.is_err(), "Expected an error for invalid URL");

    // If you want to check if it was specifically a reqwest builder error:
    let error = result.unwrap_err();
    let expected_error = format!(
        "Network error after {} attempts: builder error",
        CONFIG.max_retry
    );

    assert_eq!(error.to_string(), expected_error)
}
