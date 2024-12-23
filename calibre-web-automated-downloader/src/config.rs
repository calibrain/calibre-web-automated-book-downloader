use dotenv::dotenv;
use once_cell::sync::Lazy;
use std::env;
use std::path::PathBuf;
use std::fs;

/// List of supported book languages.
static SUPPORTED_BOOK_LANGUAGE: Lazy<Vec<&'static str>> = Lazy::new(|| {
    vec![
        "en","zh","ru","es","fr","de","it","pt","pl","bg","nl","ja","ar","he","hu",
        "la","cs","ko","tr","uk","id","ro","el","lt","bn","zh‑Hant","af","ca","sv",
        "th","hi","ga","lv","kn","sr","bo","da","fa","hr","sk","jv","vi","ur","fi",
        "no","rw","ta","be","kk","mn","ka","sl","eo","gl","mr","fil","gu","ml","ky",
        "qu","az","sw","ba","pa","ms","te","sq","ug","hy","shn"
    ]
});

/// Configuration settings for the book downloader application.
#[derive(Debug)]
pub struct Config {
    // Directory settings
    pub base_dir: PathBuf,
    pub log_dir: PathBuf,
    pub tmp_dir: PathBuf,
    pub ingest_dir: PathBuf,
    pub status_timeout: u64,

    // Network settings
    pub max_retry: u32,
    pub default_sleep: u64,
    pub cloudflare_proxy: String,
    pub use_cf_bypass: bool,

    // Anna's Archive settings
    pub aa_donator_key: String,
    pub aa_base_url: String,

    // File format settings
    pub supported_formats: Vec<String>,
    pub book_language: Vec<String>,

    // API settings
    pub flask_host: String,
    pub flask_port: u16,
    pub flask_debug: bool,

    // Logging settings
    pub log_file: PathBuf,
    pub main_loop_sleep_time: u64,
}

impl Config {
    /// Loads the configuration from environment variables and sets up directories.
    pub fn new() -> Self {
        // Load environment variables from .env file
        dotenv().ok();

        // Directory settings
        let base_dir = PathBuf::from(env::current_dir().expect("Failed to get current directory"));
        let log_dir = PathBuf::from("/var/logs");

        let tmp_dir = PathBuf::from(env::var("TMP_DIR").unwrap_or_else(|_| "/tmp/cwa-book-downloader".to_string()));
        let ingest_dir = PathBuf::from(env::var("INGEST_DIR").unwrap_or_else(|_| "/tmp/cwa-book-ingest".to_string()));
        let status_timeout = env::var("STATUS_TIMEOUT")
            .unwrap_or_else(|_| "3600".to_string())
            .parse::<u64>()
            .expect("STATUS_TIMEOUT must be a valid integer");

        // Create necessary directories
        fs::create_dir_all(&tmp_dir).expect("Failed to create TMP_DIR");
        fs::create_dir_all(&log_dir).expect("Failed to create LOG_DIR");
        fs::create_dir_all(&ingest_dir).expect("Failed to create INGEST_DIR");

        // Network settings
        let max_retry = env::var("MAX_RETRY")
            .unwrap_or_else(|_| "3".to_string())
            .parse::<u32>()
            .expect("MAX_RETRY must be a valid integer");
        let default_sleep = env::var("DEFAULT_SLEEP")
            .unwrap_or_else(|_| "5".to_string())
            .parse::<u64>()
            .expect("DEFAULT_SLEEP must be a valid integer");
        let cloudflare_proxy = env::var("CLOUDFLARE_PROXY_URL")
            .unwrap_or_else(|_| "http://localhost:8000".to_string());
        let use_cf_bypass = env::var("USE_CF_BYPASS")
            .unwrap_or_else(|_| "true".to_string())
            .to_lowercase()
            .parse::<bool>()
            .unwrap_or(true);

        // Anna's Archive settings
        let aa_donator_key = env::var("AA_DONATOR_KEY").unwrap_or_else(|_| "".to_string()).trim().to_string();
        let aa_base_url = env::var("AA_BASE_URL")
            .unwrap_or_else(|_| "https://annas-archive.org".to_string())
            .trim_end_matches('/')
            .to_string();

        // File format settings
        let supported_formats = env::var("SUPPORTED_FORMATS")
            .unwrap_or_else(|_| "epub,mobi,azw3,fb2,djvu,cbz,cbr".to_string())
            .split(',')
            .map(|s| s.trim().to_lowercase())
            .filter(|s| !s.is_empty())
            .collect::<Vec<String>>();

        let mut book_language = env::var("BOOK_LANGUAGE")
            .unwrap_or_else(|_| "en".to_string())
            .to_lowercase()
            .split(',')
            .map(|s| s.trim().to_string())
            .filter(|s| SUPPORTED_BOOK_LANGUAGE.contains(&s.as_str()))
            .collect::<Vec<String>>();

        if book_language.is_empty() {
            book_language.push("en".to_string());
        }

        // API settings
        let flask_host = env::var("FLASK_HOST").unwrap_or_else(|_| "0.0.0.0".to_string());
        let flask_port = env::var("FLASK_PORT")
            .unwrap_or_else(|_| "5003".to_string())
            .parse::<u16>()
            .expect("FLASK_PORT must be a valid integer");
        let flask_debug = env::var("FLASK_DEBUG")
            .unwrap_or_else(|_| "False".to_string())
            .to_lowercase()
            .parse::<bool>()
            .unwrap_or(false);

        // Logging settings
        let log_file = log_dir.join("cwa-bookdownloader.log");
        let main_loop_sleep_time = env::var("MAIN_LOOP_SLEEP_TIME")
            .unwrap_or_else(|_| "5".to_string())
            .parse::<u64>()
            .expect("MAIN_LOOP_SLEEP_TIME must be a valid integer");

        Config {
            base_dir,
            log_dir,
            tmp_dir,
            ingest_dir,
            status_timeout,
            max_retry,
            default_sleep,
            cloudflare_proxy,
            use_cf_bypass,
            aa_donator_key,
            aa_base_url,
            supported_formats,
            book_language,
            flask_host,
            flask_port,
            flask_debug,
            log_file,
            main_loop_sleep_time,
        }
    }
}

/// A global, lazily-initialized configuration instance.
pub static CONFIG: Lazy<Config> = Lazy::new(|| Config::new());
