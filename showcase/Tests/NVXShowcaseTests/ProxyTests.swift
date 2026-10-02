import Darwin
import Foundation
import Testing
@testable import NVXCore

@Suite(.serialized)
struct ProxyTests {
    @Test func startsOnLoopbackAndReturnsActualPort() throws {
        let proxy = NVXProxy()
        defer { proxy.stop() }
        let port = try proxy.start()
        #expect(port > 0)
        #expect(try proxy.start() == port)
        let socket = try connectToProxy(port)
        defer { Darwin.close(socket) }
        var address = sockaddr_in()
        var length = socklen_t(MemoryLayout<sockaddr_in>.size)
        let result = withUnsafeMutablePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                getpeername(socket, $0, &length)
            }
        }
        #expect(result == 0)
        #expect(address.sin_addr.s_addr == inet_addr("127.0.0.1"))
    }

    @Test func acceptsHTTPConnection() async throws {
        let proxy = NVXProxy()
        defer { proxy.stop() }
        let port = try proxy.start()
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 3
        let session = URLSession(configuration: config)
        defer { session.invalidateAndCancel() }
        let (data, response) = try await session.data(from: URL(string: "http://127.0.0.1:\(port)/unsupported")!)
        #expect((response as? HTTPURLResponse)?.statusCode == 404)
        #expect(String(decoding: data, as: UTF8.self).contains("Unsupported API path"))
    }

    @Test func stopsAndClosesListenerAndActiveConnections() throws {
        let proxy = NVXProxy()
        let port = try proxy.start()
        let active = try connectToProxy(port)
        defer { Darwin.close(active) }
        // Ensure the connection has actually been accepted before stopping it.
        let response = try exchange(port, "GET /unsupported HTTP/1.1\r\nHost: localhost\r\n\r\n")
        #expect(response.status == 404)
        proxy.stop()
        proxy.stop()
        #expect(throws: (any Error).self) { try connectToProxy(port) }
        var byte: UInt8 = 0
        let read = Darwin.recv(active, &byte, 1, 0)
        #expect(read == 0 || (read < 0 && errno == ECONNRESET))

        // A fresh instance can bind the exact same port immediately after stop.
        let replacement = NVXProxy(port: port)
        defer { replacement.stop() }
        #expect(try replacement.start() == port)
    }

    @Test func restartsAfterStop() throws {
        let proxy = NVXProxy()
        defer { proxy.stop() }
        proxy.stop()
        #expect(try proxy.start() > 0)
        proxy.stop()
        let port = try proxy.start()
        #expect(try exchange(port, "GET /unsupported HTTP/1.1\r\nHost: localhost\r\n\r\n").status == 404)
    }

    @Test func occupiedPortThrows() throws {
        let first = NVXProxy()
        defer { first.stop() }
        let second = NVXProxy(port: try first.start())
        defer { second.stop() }
        #expect(throws: (any Error).self) { try second.start() }
    }

    @Test func releasesListenerOnDeinit() throws {
        var proxy: NVXProxy? = NVXProxy()
        let listeningPort = try proxy?.start()
        let port = try #require(listeningPort)
        proxy = nil
        #expect(throws: (any Error).self) { try connectToProxy(port) }
    }

    @Test func routesProvidersAndReplacesCallerCredentials() throws {
        let fixture = UpstreamFixture()
        MockUpstream.fixture = fixture
        defer { MockUpstream.fixture = nil }
        let proxy = makeMockProxy()
        defer { proxy.stop() }
        let port = try proxy.start()
        let body = "{\"model\":\"test\"}"
        for path in ["/v1/messages?test=1", "/v1/messages/count_tokens", "/v1/chat/completions", "/v1/responses"] {
            let response = try exchange(port, "POST \(path) HTTP/1.1\r\nHost: attacker.invalid\r\nAuthorization: Bearer caller-secret\r\nx-api-key: caller-secret\r\nanthropic-version: 2023-06-01\r\nContent-Type: application/json\r\nConnection: close, x-private\r\nx-private: remove-me\r\nContent-Length: \(body.utf8.count)\r\n\r\n\(body)")
            #expect(response.status == 201)
            #expect(response.body == fixture.responseBody)
            #expect(response.headers["x-upstream-test"] == "preserved")
            #expect(response.headers["connection"] == "close")
            #expect(response.headers["x-hop"] == nil)
            #expect(response.headers["content-length"] == String(fixture.responseBody.count))
        }
        let requests = fixture.requests
        #expect(requests.count == 4)
        for request in requests {
            let anthropic = request.url!.path.hasPrefix("/v1/messages")
            #expect(request.url?.scheme == "https")
            #expect(request.url?.host == (anthropic ? "api.anthropic.com" : "api.openai.com"))
            #expect(request.value(forHTTPHeaderField: "Authorization") == (anthropic ? nil : "Bearer host-openai"))
            #expect(request.value(forHTTPHeaderField: "x-api-key") == (anthropic ? "host-anthropic" : nil))
            #expect(request.value(forHTTPHeaderField: "x-private") == nil)
            #expect(request.value(forHTTPHeaderField: "Content-Type") == "application/json")
            #expect(request.httpBody == Data(body.utf8))
        }
        #expect(requests.first?.url?.query == "test=1")
    }

    @Test func decodesChunkedRequestBody() throws {
        let fixture = UpstreamFixture()
        MockUpstream.fixture = fixture
        defer { MockUpstream.fixture = nil }
        let proxy = makeMockProxy()
        defer { proxy.stop() }
        let response = try exchange(try proxy.start(), "POST /v1/responses HTTP/1.1\r\nHost: localhost\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc\r\n2;test=value\r\nde\r\n0\r\nX-Trailer: ignored\r\n\r\n")
        #expect(response.status == 201)
        #expect(fixture.requests.first?.httpBody == Data("abcde".utf8))
        #expect(fixture.requests.first?.value(forHTTPHeaderField: "Transfer-Encoding") == nil)
    }

    @Test func rejectsMissingCredentialsAndAmbiguousFraming() throws {
        let fixture = UpstreamFixture()
        MockUpstream.fixture = fixture
        defer { MockUpstream.fixture = nil }
        let proxy = makeMockProxy(environment: [:])
        defer { proxy.stop() }
        let port = try proxy.start()
        #expect(try exchange(port, "POST /v1/messages HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n").status == 503)
        for headers in ["Content-Length: 0\r\nContent-Length: 1", "Content-Length: 0\r\nTransfer-Encoding: chunked"] {
            #expect(try exchange(port, "POST /v1/responses HTTP/1.1\r\nHost: localhost\r\n\(headers)\r\n\r\n").status == 400)
        }
        #expect(try exchange(port, "POST /v1/responses HTTP/1.1\r\nContent-Length: 20000000\r\n\r\n").status == 413)
        #expect(fixture.requests.isEmpty)
    }

    @Test func returnsUpstreamFailuresWithoutExposingCredentials() throws {
        let fixture = UpstreamFixture()
        fixture.failure = true
        MockUpstream.fixture = fixture
        defer { MockUpstream.fixture = nil }
        let proxy = makeMockProxy()
        defer { proxy.stop() }
        let response = try exchange(try proxy.start(), "GET /v1/models HTTP/1.1\r\nHost: localhost\r\n\r\n")
        #expect(response.status == 502)
        #expect(String(decoding: response.body, as: UTF8.self) == "Upstream request failed\n")
    }
}

private func makeMockProxy(environment: [String: String] = [
    "ANTHROPIC_API_KEY": "host-anthropic", "OPENAI_API_KEY": "host-openai"
]) -> NVXProxy {
    let config = URLSessionConfiguration.ephemeral
    config.protocolClasses = [MockUpstream.self]
    return NVXProxy(configuration: config, environment: { environment })
}

private final class UpstreamFixture: @unchecked Sendable {
    private let lock = NSLock()
    private var captured: [URLRequest] = []
    let responseBody = Data([0, 1, 2, 255, 10])
    var failure = false
    var requests: [URLRequest] { lock.withLock { captured } }
    func record(_ request: URLRequest) { lock.withLock { captured.append(request) } }
}

private final class MockUpstream: URLProtocol, @unchecked Sendable {
    private static let lock = NSLock()
    private static var stored: UpstreamFixture?
    static var fixture: UpstreamFixture? {
        get { lock.withLock { stored } }
        set { lock.withLock { stored = newValue } }
    }
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        guard let fixture = Self.fixture else {
            client?.urlProtocol(self, didFailWithError: URLError(.badServerResponse))
            return
        }
        var captured = request
        if captured.httpBody == nil, let stream = captured.httpBodyStream {
            stream.open()
            defer { stream.close() }
            var body = Data(), bytes = [UInt8](repeating: 0, count: 1024)
            while true {
                let count = stream.read(&bytes, maxLength: bytes.count)
                if count <= 0 { break }
                body.append(contentsOf: bytes.prefix(count))
            }
            captured.httpBody = body
        }
        fixture.record(captured)
        if fixture.failure {
            client?.urlProtocol(self, didFailWithError: URLError(.cannotConnectToHost))
            return
        }
        let response = HTTPURLResponse(url: request.url!, statusCode: 201, httpVersion: "HTTP/1.1",
                                       headerFields: ["Content-Type": "application/octet-stream",
                                                      "X-Upstream-Test": "preserved",
                                                      "Connection": "keep-alive, x-hop",
                                                      "X-Hop": "remove-me",
                                                      "Content-Length": String(fixture.responseBody.count)])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: fixture.responseBody)
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

private func connectToProxy(_ port: UInt16) throws -> Int32 {
    let fd = Darwin.socket(AF_INET, SOCK_STREAM, 0)
    guard fd >= 0 else { throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno)) }
    var timeout = timeval(tv_sec: 3, tv_usec: 0)
    _ = setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, socklen_t(MemoryLayout.size(ofValue: timeout)))
    _ = setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, socklen_t(MemoryLayout.size(ofValue: timeout)))
    var yes: Int32 = 1
    _ = setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &yes, socklen_t(MemoryLayout.size(ofValue: yes)))
    var address = sockaddr_in()
    address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
    address.sin_family = sa_family_t(AF_INET)
    address.sin_port = port.bigEndian
    address.sin_addr.s_addr = inet_addr("127.0.0.1")
    let result = withUnsafePointer(to: &address) {
        $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
            Darwin.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
        }
    }
    guard result == 0 else {
        let error = NSError(domain: NSPOSIXErrorDomain, code: Int(errno))
        Darwin.close(fd)
        throw error
    }
    return fd
}

private struct ProxyResponse {
    let status: Int
    let headers: [String: String]
    let body: Data
}

private func exchange(_ port: UInt16, _ request: String) throws -> ProxyResponse {
    let fd = try connectToProxy(port)
    defer { Darwin.close(fd) }
    let data = Data(request.utf8)
    try data.withUnsafeBytes { buffer in
        var sent = 0
        while sent < buffer.count {
            let count = Darwin.send(fd, buffer.baseAddress!.advanced(by: sent), buffer.count - sent, 0)
            guard count > 0 else { throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno)) }
            sent += count
        }
    }
    var received = Data(), bytes = [UInt8](repeating: 0, count: 4096)
    while true {
        let count = Darwin.recv(fd, &bytes, bytes.count, 0)
        if count == 0 { break }
        guard count > 0 else { throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno)) }
        received.append(contentsOf: bytes.prefix(count))
    }
    let end = try #require(received.range(of: Data("\r\n\r\n".utf8)))
    let lines = String(decoding: received[..<end.lowerBound], as: UTF8.self).components(separatedBy: "\r\n")
    let status = try #require(Int(lines[0].split(separator: " ")[1]))
    var headers: [String: String] = [:]
    for line in lines.dropFirst() {
        let parts = line.split(separator: ":", maxSplits: 1)
        headers[parts[0].lowercased()] = parts[1].trimmingCharacters(in: .whitespaces)
    }
    return ProxyResponse(status: status, headers: headers, body: Data(received[end.upperBound...]))
}
