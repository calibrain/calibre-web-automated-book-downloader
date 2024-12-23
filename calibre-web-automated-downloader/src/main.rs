mod config;

use config::CONFIG;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Access configuration settings using the global CONFIG instance
    let config = CONFIG.as_ref().expect("Failed to load configuration");

    println!("Base Directory: {:?}", config.base_dir);

    Ok(())
}
