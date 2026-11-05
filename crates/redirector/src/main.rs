use std::{env, net::Ipv4Addr, sync::Arc};

use redirector::{BlazeEndpoint, ServerInstanceRequest, http, render_server_instance};
use tls::TlsAcceptor;
use tokio::net::TcpListener;

struct Config {
    listen_port: u16,
    certificate: String,
    private_key: String,
    blaze: BlazeEndpoint,
}

fn env_or(name: &str, default: &str) -> String {
    env::var(name).unwrap_or_else(|_| default.to_owned())
}

fn load_config() -> Config {
    let blaze_ip: Ipv4Addr = env_or("BLAZE_IP", "127.0.0.1")
        .parse()
        .expect("BLAZE_IP invalide");
    Config {
        listen_port: env_or("REDIRECTEUR_PORT", "42230")
            .parse()
            .expect("REDIRECTEUR_PORT invalide"),
        certificate: env_or("TLS_CERT", "data/certs/test.crt"),
        private_key: env_or("TLS_KEY", "data/certs/test.key"),
        blaze: BlazeEndpoint {
            hostname: env_or("BLAZE_HOST", "127.0.0.1"),
            ip: blaze_ip.octets(),
            port: env_or("BLAZE_PORT", "10041")
                .parse()
                .expect("BLAZE_PORT invalide"),
            secure: env_or("BLAZE_SECURE", "1") == "1",
        },
    }
}

async fn answer(
    acceptor: TlsAcceptor,
    socket: tokio::net::TcpStream,
    response_body: Arc<String>,
) -> std::io::Result<()> {
    let peer = socket.peer_addr()?;
    let incoming = tls::LazyConfigAcceptor::new(tls::ClientHelloAcceptor::default(), socket)
        .await
        .map_err(|error| {
            std::io::Error::new(error.kind(), format!("TLS before ClientHello: {error}"))
        })?;
    {
        let hello = incoming.client_hello();
        println!(
            "TLS ClientHello {peer}: SNI={:?}, cipher suites={:?}, signature schemes={:?}",
            hello.server_name(),
            hello.cipher_suites(),
            hello.signature_schemes()
        );
    }
    let mut stream = incoming
        .into_stream(acceptor.config().clone())
        .await
        .map_err(|error| {
            std::io::Error::new(error.kind(), format!("TLS after ClientHello: {error}"))
        })?;
    println!(
        "TLS established {peer}: version={:?}, cipher suite={:?}",
        stream.get_ref().1.protocol_version(),
        stream
            .get_ref()
            .1
            .negotiated_cipher_suite()
            .map(|suite| suite.suite())
    );
    let request = http::read_request(&mut stream).await?;
    if request.method != "POST" || request.path != "/redirector/getServerInstance" {
        println!("requête inconnue : {} {}", request.method, request.path);
        return http::write_response(&mut stream, "404 Not Found", "").await;
    }
    match ServerInstanceRequest::parse(&request.body) {
        Some(instance) => println!(
            "getServerInstance : service {}, client {}, profil {}",
            instance.service_name, instance.client_version, instance.connection_profile
        ),
        None => println!("getServerInstance sans nom de service :\n{}", request.body),
    }
    http::write_response(&mut stream, "200 OK", &response_body).await
}

#[tokio::main]
async fn main() -> std::io::Result<()> {
    let config = load_config();
    let acceptor = tls::acceptor_from_pem(&config.certificate, &config.private_key);
    let response_body = Arc::new(render_server_instance(&config.blaze));
    let listen_host = env_or("LISTEN_HOST", if cfg!(windows) { "0.0.0.0" } else { "::" });
    let listener = TcpListener::bind((listen_host.as_str(), config.listen_port)).await?;
    println!(
        "redirector sur {}:{} -> Blaze {}:{}",
        listen_host, config.listen_port, config.blaze.hostname, config.blaze.port
    );
    loop {
        let (socket, peer) = listener.accept().await?;
        let acceptor = acceptor.clone();
        let response_body = response_body.clone();
        tokio::spawn(async move {
            if let Err(error) = answer(acceptor, socket, response_body).await {
                println!("{peer} : {error}");
            }
        });
    }
}
