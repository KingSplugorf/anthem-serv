use std::sync::Arc;

use rustls::pki_types::{CertificateDer, PrivateKeyDer, pem::PemObject};
pub use rustls::server::Acceptor as ClientHelloAcceptor;
pub use tokio_rustls::{LazyConfigAcceptor, TlsAcceptor};

pub fn acceptor_from_pem(certificate_path: &str, private_key_path: &str) -> TlsAcceptor {
    let certificates = CertificateDer::pem_file_iter(certificate_path)
        .expect("certificat illisible")
        .collect::<Result<Vec<_>, _>>()
        .expect("certificat invalide");
    let key = PrivateKeyDer::from_pem_file(private_key_path).expect("clé privée illisible");
    let config = rustls::ServerConfig::builder_with_provider(Arc::new(
        rustls::crypto::ring::default_provider(),
    ))
    .with_safe_default_protocol_versions()
    .expect("versions TLS")
    .with_no_client_auth()
    .with_single_cert(certificates, key)
    .expect("certificat refusé par rustls");
    TlsAcceptor::from(Arc::new(config))
}
