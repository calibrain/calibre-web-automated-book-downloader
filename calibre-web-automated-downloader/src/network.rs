use crate::config::CONFIG;
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

pub async fn html_get_page(url: String) -> Result<String, reqwest::Error> {
    let client = Client::new();

    for n in 0..CONFIG.max_retry {
        println!("Attempt {}: GET {}", n + 1, url);

        let response = client
            .get(&url)
            .header("User-Agent", APP_USER_AGENT)
            .send()
            .await?
            .error_for_status()?;

        let result = response.text().await;

        match result {
            Ok(body) => return Ok(body),
            Err(e) => {
                if n + 1 >= CONFIG.max_retry {
                    return Err(e);
                }
                let delay = Duration::from_secs(CONFIG.retry_wait_duration);

                println!(
                    "Error: {}. Retrying in {} seconds...",
                    e, CONFIG.retry_wait_duration
                );
                tokio::time::sleep(delay).await;
            }
        }
    }

    Ok("".to_string())
}

/* TESTS */

/// Test that `html_get_page` successfully retrieves content when the server responds with 200 OK.
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

/// Test that `html_get_page` retries upon encountering transient server errors and eventually succeeds.
#[tokio::test]
async fn test_html_get_page_retry_then_success() {
    // Start a mock server
    let mock_server = MockServer::start().await;

    // Define the expected successful response
    let expected_body = "<html><body>Recovered</body></html>";

    // Configure the mock to respond with 500 Internal Server Error for the first two requests
    Mock::given(method("GET"))
        .and(path("/flaky"))
        .respond_with(ResponseTemplate::new(500))
        .expect(2) // Expect this mock to be called twice
        .mount(&mock_server)
        .await;

    // Configure the mock to respond with 200 OK on the third attempt
    Mock::given(method("GET"))
        .and(path("/flaky"))
        .and(header("User-Agent", APP_USER_AGENT))
        .respond_with(ResponseTemplate::new(200).set_body_string(expected_body))
        .mount(&mock_server)
        .await;

    // Construct the URL for the mock endpoint
    let url = format!("{}/flaky", &mock_server.uri());

    // Call the `html_get_page` function
    let result = html_get_page(url).await;
    let result_str = result.unwrap();
    println!("{}", result_str);

    // Assert that the result matches the expected body
    assert_eq!(result_str, expected_body);
}

/// Test that `html_get_page` fails gracefully after exhausting all retry attempts.
#[tokio::test]
async fn test_html_get_page_retry_exhausted() {
    // Start a mock server
    let mock_server = MockServer::start().await;

    // Configure the mock to always respond with 500 Internal Server Error
    Mock::given(method("GET"))
        .and(path("/always_fail"))
        .respond_with(ResponseTemplate::new(500))
        .expect(5) // Depending on `CONFIG.max_retry_duration`, adjust expected calls
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

    // Optionally, verify the error type or message
    let error = result.unwrap_err();
    assert!(
        error.is_timeout(),
        "Expected a server error, got a different error"
    );
}

/// Test that `html_get_page` handles invalid URLs appropriately.
#[tokio::test]
async fn test_html_get_page_invalid_url() {
    // Define an invalid URL
    let invalid_url = "http://".to_string(); // Invalid URL

    // Call the `html_get_page` function
    let result = html_get_page(invalid_url).await;

    // Assert that the result is an error
    assert!(result.is_err(), "Expected an error for invalid URL");

    // Optionally, verify the error type or message
    let error = result.unwrap_err();
    assert!(
        error.is_builder(),
        "Expected a builder error for invalid URL, got a different error"
    );
}
