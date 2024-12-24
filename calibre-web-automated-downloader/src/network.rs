use crate::config::CONFIG;
use anyhow::{anyhow, Result};
use axum::body::Bytes;
use reqwest;
use reqwest::Client;
use std::time::Duration;
use tokio;
use url::Url;
use wiremock::matchers::{method, path};
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

pub async fn html_get_page_cf(url: String) -> Result<String> {
    if CONFIG.use_cf_bypass {
        return html_get_page(url).await;
    } else {
        let cf_url = format!("{}/html?url={}", CONFIG.cloudflare_proxy, url);
        return html_get_page(cf_url).await;
    }
}

pub async fn download_url(url: &str) -> Result<Bytes> {
    let response = Client::new().get(url).send().await?; // Send the HTTP GET request

    // Directly return the body as a Bytes object
    Ok(response.bytes().await?)
}

fn get_absolute_url(base_url: &str, url: &str) -> Result<String> {
    // If the URL is empty, return an empty string
    if url.trim().is_empty() {
        return Ok(String::new());
    }

    // If the URL already starts with "http", return it as is
    if url.starts_with("http") {
        return Ok(url.to_string());
    }

    // Parse the base and relative URLs
    let parsed_base = Url::parse(base_url)
        .map_err(|e| anyhow!("Failed to parse base URL '{}': {}", base_url, e))?;
    let mut parsed_url = Url::parse(url).unwrap_or_else(|_| parsed_base.join(url).unwrap());

    // If the parsed URL lacks scheme or host, fill them in from the base URL
    if parsed_url.scheme().is_empty() || parsed_url.host_str().is_none() {
        parsed_url.set_scheme(parsed_base.scheme()).ok();
        parsed_url.set_host(parsed_base.host_str()).ok();
        if let Some(port) = parsed_base.port() {
            parsed_url.set_port(Some(port)).ok();
        }
    }

    Ok(parsed_url.to_string())
}

/* TESTS */
#[cfg(test)]
mod tests {
    use super::*;
    use tokio::test;

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

    ///
    #[test]
    async fn test_empty_url() {
        let base_url = "https://example.com";
        let relative_url = "";
        let result = get_absolute_url(base_url, relative_url).unwrap();
        assert_eq!(result, "");
    }

    #[test]
    async fn test_absolute_url() {
        let base_url = "https://example.com";
        let absolute_url = "https://another.com/path";
        let result = get_absolute_url(base_url, absolute_url).unwrap();
        assert_eq!(result, "https://another.com/path");
    }

    #[test]
    async fn test_relative_url() {
        let base_url = "https://example.com";
        let relative_url = "/some/path";
        let result = get_absolute_url(base_url, relative_url).unwrap();
        assert_eq!(result, "https://example.com/some/path");
    }

    #[test]
    async fn test_relative_url_with_base_path() {
        let base_url = "https://example.com/base/";
        let relative_url = "another/path";
        let result = get_absolute_url(base_url, relative_url).unwrap();
        assert_eq!(result, "https://example.com/base/another/path");
    }

    #[test]
    async fn test_relative_url_with_trailing_slash_base() {
        let base_url = "https://example.com/";
        let relative_url = "some/path";
        let result = get_absolute_url(base_url, relative_url).unwrap();
        assert_eq!(result, "https://example.com/some/path");
    }

    #[test]
    async fn test_missing_scheme() {
        let base_url = "https://example.com";
        let relative_url = "//another.com/path";
        let result = get_absolute_url(base_url, relative_url).unwrap();
        assert_eq!(result, "https://another.com/path");
    }

    #[test]
    async fn test_invalid_base_url() {
        let base_url = "not-a-valid-url";
        let relative_url = "/some/path";
        let result = get_absolute_url(base_url, relative_url);
        assert!(result.is_err());
    }
}
