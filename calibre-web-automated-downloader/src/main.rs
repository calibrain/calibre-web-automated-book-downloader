mod config;
mod network;

use config::CONFIG;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Access configuration settings using the global CONFIG instance
    println!("STATUS_TIMEOUT: {:?}", CONFIG.status_timeout);

    Ok(())
}
