import Darwin
import Dispatch
import Foundation

/// A loopback-only HTTP/1.x credential shield. Requests and upstream responses
/// are buffered; each client connection serves one request and is then closed.
public final class NVXProxy: @unchecked Sendable {
    private let queue = DispatchQueue(label: "NVXProxy")
    private let queueKey = DispatchSpecificKey<Bool>()
    private let requestedPort: UInt16
    private let configuration: URLSessionConfiguration
    private let environment: @Sendable () -> [String: String]
    private var listener: DispatchSourceRead?
    private var listenerClosed: DispatchGroup?
    private var port: UInt16 = 0
    private var session: URLSession?
    private var clients: [UUID: Client] = [:]

    public convenience init(port: UInt16 = 0) {
        self.init(port: port, configuration: .ephemeral,
                  environment: { ProcessInfo.processInfo.environment })
    }

    // Allows deterministic forwarding tests without external API calls or keys.
    internal init(port: UInt16 = 0, configuration: URLSessionConfiguration,
                  environment: @escaping @Sendable () -> [String: String]) {
        self.requestedPort = port
        self.configuration = configuration.copy() as! URLSessionConfiguration
        self.environment = environment
        queue.setSpecific(key: queueKey, value: true)
    }

    /// Starts listening on 127.0.0.1. Repeated calls return the existing port.
    public func start() throws -> UInt16 {
        try queue.sync {
            if listener != nil { return port }
            let fd = Darwin.socket(AF_INET, SOCK_STREAM, 0)
            guard fd >= 0 else { throw posixError() }
            var ownsDescriptor = true
            defer { if ownsDescriptor { Darwin.close(fd) } }
            try configureSocket(fd)
            var reuse: Int32 = 1
            guard setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &reuse,
                             socklen_t(MemoryLayout.size(ofValue: reuse))) == 0 else {
                throw posixError()
            }
            var address = sockaddr_in()
            address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
            address.sin_family = sa_family_t(AF_INET)
            address.sin_port = requestedPort.bigEndian
            address.sin_addr.s_addr = inet_addr("127.0.0.1")
            let bound = withUnsafePointer(to: &address) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    Darwin.bind(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
                }
            }
            guard bound == 0, Darwin.listen(fd, 128) == 0 else { throw posixError() }
            var length = socklen_t(MemoryLayout<sockaddr_in>.size)
            let named = withUnsafeMutablePointer(to: &address) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    getsockname(fd, $0, &length)
                }
            }
            guard named == 0 else { throw posixError() }

            let config = configuration.copy() as! URLSessionConfiguration
            config.urlCache = nil
            config.httpCookieStorage = nil
            config.urlCredentialStorage = nil
            config.requestCachePolicy = .reloadIgnoringLocalCacheData
            config.timeoutIntervalForRequest = 60
            config.timeoutIntervalForResource = 300
            session = URLSession(configuration: config, delegate: NoRedirects(), delegateQueue: nil)
            let source = DispatchSource.makeReadSource(fileDescriptor: fd, queue: queue)
            let closed = DispatchGroup()
            closed.enter()
            source.setEventHandler { [weak self] in self?.acceptClients(fd) }
            source.setCancelHandler { Darwin.close(fd); closed.leave() }
            listener = source
            listenerClosed = closed
            port = UInt16(bigEndian: address.sin_port)
            ownsDescriptor = false
            source.resume()
            return port
        }
    }

    /// Cancels active requests and closes all sockets. Safe to call repeatedly.
    public func stop() {
        let onQueue = DispatchQueue.getSpecific(key: queueKey) == true
        let cleanup = {
            var closed: [DispatchGroup] = []
            if let group = self.listenerClosed { closed.append(group) }
            self.listener?.cancel()
            self.listener = nil
            self.listenerClosed = nil
            self.port = 0
            for client in Array(self.clients.values) {
                closed.append(client.closed)
                client.finish()
            }
            self.session?.invalidateAndCancel()
            self.session = nil
            return closed
        }
        let closed = onQueue ? cleanup() : queue.sync(execute: cleanup)
        // Cancellation handlers run on the I/O queue. Never wait on that queue.
        if !onQueue { for group in closed { group.wait() } }
    }

    deinit { stop() }

    private func acceptClients(_ fd: Int32) {
        guard listener != nil else { return }
        while true {
            let socket = Darwin.accept(fd, nil, nil)
            if socket < 0 {
                if errno == EINTR { continue }
                return
            }
            do {
                try configureSocket(socket)
                let client = Client(fd: socket, queue: queue, owner: self)
                clients[client.id] = client
                client.readRequest()
            } catch { Darwin.close(socket) }
        }
    }

    private func forward(_ request: Request, client: Client) {
        guard let session else { client.finish(); return }
        let path = request.target.split(separator: "?", maxSplits: 1).first.map(String.init) ?? ""
        guard path.hasPrefix("/v1/") else {
            client.respond(status: 404, message: "Unsupported API path")
            return
        }
        let anthropic = path == "/v1/messages" || path.hasPrefix("/v1/messages/")
        let keyName = anthropic ? "ANTHROPIC_API_KEY" : "OPENAI_API_KEY"
        guard let key = environment()[keyName], !key.isEmpty,
              !key.contains("\r"), !key.contains("\n") else {
            client.respond(status: 503, message: "Host API credential is unavailable")
            return
        }
        let origin = anthropic ? "https://api.anthropic.com" : "https://api.openai.com"
        guard let url = URL(string: origin + request.target),
              url.host == (anthropic ? "api.anthropic.com" : "api.openai.com") else {
            client.respond(status: 400, message: "Invalid request target")
            return
        }
        var upstream = URLRequest(url: url)
        upstream.httpMethod = request.method
        upstream.httpBody = request.body.isEmpty ? nil : request.body
        let excluded = hopByHop.union(["host", "content-length", "authorization", "x-api-key",
                                       "proxy-authorization", "expect", "accept-encoding"])
            .union(connectionHeaders(request.headers))
        for (name, value) in request.headers where !excluded.contains(name) {
            upstream.setValue(value, forHTTPHeaderField: name)
        }
        upstream.setValue("identity", forHTTPHeaderField: "Accept-Encoding")
        if anthropic {
            upstream.setValue(key, forHTTPHeaderField: "x-api-key")
            if upstream.value(forHTTPHeaderField: "anthropic-version") == nil {
                upstream.setValue("2023-06-01", forHTTPHeaderField: "anthropic-version")
            }
        } else {
            upstream.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
        }
        let callbackQueue = queue
        let task = session.dataTask(with: upstream) { [weak client] data, response, error in
            callbackQueue.async { [weak client] in
                guard let client, !client.finished else { return }
                guard error == nil, let response = response as? HTTPURLResponse else {
                    client.respond(status: 502, message: "Upstream request failed")
                    return
                }
                var headers: [String: String] = [:]
                for (name, value) in response.allHeaderFields {
                    headers[String(describing: name).lowercased()] = String(describing: value)
                }
                // URLSession decodes compressed bodies; frame the bytes we return.
                let excluded = hopByHop.union(["content-length", "content-encoding"])
                    .union(connectionHeaders(headers))
                headers = headers.filter { !excluded.contains($0.key) }
                client.respond(status: response.statusCode, headers: headers,
                               body: data ?? Data(), head: request.method == "HEAD",
                               headLength: response.value(forHTTPHeaderField: "Content-Length"))
            }
        }
        client.task = task
        task.resume()
    }

    private final class Client {
        let id = UUID()
        let closed = DispatchGroup()
        var finished = false
        var task: URLSessionDataTask?
        private let queue: DispatchQueue
        private weak var owner: NVXProxy?
        private let io: DispatchIO
        private var input = Data()
        private var forwarded = false
        private var sentContinue = false
        private var timer: DispatchSourceTimer?

        init(fd: Int32, queue: DispatchQueue, owner: NVXProxy) {
            self.queue = queue
            self.owner = owner
            let closed = self.closed
            closed.enter()
            io = DispatchIO(type: .stream, fileDescriptor: fd, queue: queue) { _ in
                Darwin.close(fd)
                closed.leave()
            }
            io.setLimit(lowWater: 1)
        }

        func readRequest() {
            let timer = DispatchSource.makeTimerSource(queue: queue)
            timer.schedule(deadline: .now() + 30)
            timer.setEventHandler { [weak self] in self?.finish() }
            self.timer = timer
            timer.resume()
            io.read(offset: 0, length: Int.max, queue: queue) { [weak self] done, data, error in
                guard let self, !self.finished else { return }
                if error != 0 { self.finish(); return }
                guard !self.forwarded else { return }
                if let data { self.input.append(contentsOf: data) }
                do {
                    if let request = try Request.parse(self.input) {
                        self.forwarded = true
                        self.input = Data()
                        self.timer?.cancel()
                        self.timer = nil
                        self.owner?.forward(request, client: self)
                    } else if done {
                        self.respond(status: 400, message: "Incomplete HTTP request")
                    } else if !self.sentContinue,
                              let end = self.input.range(of: Data("\r\n\r\n".utf8)),
                              String(decoding: self.input[..<end.lowerBound], as: UTF8.self)
                                .lowercased().contains("\r\nexpect: 100-continue") {
                        self.sentContinue = true
                        let interim = Data("HTTP/1.1 100 Continue\r\n\r\n".utf8)
                            .withUnsafeBytes { DispatchData(bytes: $0) }
                        self.io.write(offset: 0, data: interim,
                                      queue: self.queue) { _, _, _ in }
                    }
                } catch let error as HTTPError {
                    self.respond(status: error.status, message: error.message)
                } catch { self.respond(status: 400, message: "Malformed HTTP request") }
            }
        }

        func respond(status: Int, message: String) {
            respond(status: status, headers: ["content-type": "text/plain; charset=utf-8"],
                    body: Data((message + "\n").utf8))
        }

        func respond(status: Int, headers: [String: String] = [:], body: Data,
                     head: Bool = false, headLength: String? = nil) {
            guard !finished else { return }
            forwarded = true
            let noBody = head || status == 204 || status == 304 || (100..<200).contains(status)
            var response = "HTTP/1.1 \(status) \(HTTPURLResponse.localizedString(forStatusCode: status))\r\n"
            for (name, value) in headers.sorted(by: { $0.key < $1.key })
                where !name.contains("\r") && !name.contains("\n") && !value.contains("\r") && !value.contains("\n") {
                response += "\(name): \(value)\r\n"
            }
            if head, let headLength, Int(headLength) != nil {
                response += "Content-Length: \(headLength)\r\n"
            } else if !noBody {
                response += "Content-Length: \(body.count)\r\n"
            }
            response += "Connection: close\r\n\r\n"
            var bytes = Data(response.utf8)
            if !noBody { bytes.append(body) }
            let output = bytes.withUnsafeBytes { DispatchData(bytes: $0) }
            io.write(offset: 0, data: output, queue: queue) { [weak self] done, _, error in
                if done || error != 0 { self?.finish() }
            }
        }

        func finish() {
            guard !finished else { return }
            finished = true
            timer?.cancel()
            timer = nil
            task?.cancel()
            task = nil
            io.close(flags: .stop)
            owner?.clients.removeValue(forKey: id)
        }
    }
}

private let hopByHop: Set<String> = ["connection", "keep-alive", "proxy-authenticate",
    "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade"]

private func connectionHeaders(_ headers: [String: String]) -> Set<String> {
    Set((headers["connection"] ?? "").split(separator: ",")
        .map { $0.trimmingCharacters(in: .whitespaces).lowercased() })
}

private func posixError() -> NSError { NSError(domain: NSPOSIXErrorDomain, code: Int(errno)) }

private func configureSocket(_ fd: Int32) throws {
    let flags = fcntl(fd, F_GETFL)
    guard flags >= 0, fcntl(fd, F_SETFL, flags | O_NONBLOCK) == 0,
          fcntl(fd, F_SETFD, FD_CLOEXEC) == 0 else { throw posixError() }
    var yes: Int32 = 1
    guard setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &yes,
                     socklen_t(MemoryLayout.size(ofValue: yes))) == 0 else { throw posixError() }
}

private final class NoRedirects: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        // Keep host credentials on their fixed origin and return the upstream 3xx.
        completionHandler(nil)
    }
}

private struct HTTPError: Error {
    let status: Int
    let message: String
}

private struct Request {
    let method: String
    let target: String
    let headers: [String: String]
    let body: Data

    static func parse(_ data: Data) throws -> Request? {
        let maxHeader = 64 * 1024, maxBody = 16 * 1024 * 1024
        func bad(_ message: String) -> HTTPError { HTTPError(status: 400, message: message) }
        guard let end = data.range(of: Data("\r\n\r\n".utf8)) else {
            if data.count > maxHeader { throw HTTPError(status: 431, message: "Headers too large") }
            return nil
        }
        guard end.upperBound <= maxHeader else { throw HTTPError(status: 431, message: "Headers too large") }
        let lines = String(decoding: data[..<end.lowerBound], as: UTF8.self).components(separatedBy: "\r\n")
        let first = (lines.first ?? "").split(separator: " ", omittingEmptySubsequences: false)
        guard first.count == 3, first[2] == "HTTP/1.1" || first[2] == "HTTP/1.0",
              !first[0].isEmpty, first[0].allSatisfy({ $0.isASCII && $0.isLetter }),
              first[1].hasPrefix("/"), !first[1].contains("#"),
              first[1].allSatisfy({ $0.isASCII && !$0.isWhitespace && !$0.isNewline }) else {
            throw bad("Invalid request line")
        }
        var headers: [String: String] = [:]
        let token = Set("!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
        for line in lines.dropFirst() {
            guard let colon = line.firstIndex(of: ":"), colon != line.startIndex,
                  line[..<colon].allSatisfy({ token.contains($0) }) else { throw bad("Invalid header") }
            let name = line[..<colon].lowercased()
            let value = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
            guard value.unicodeScalars.allSatisfy({ $0.value == 9 || $0.value >= 32 && $0.value != 127 }) else {
                throw bad("Invalid header value")
            }
            if let existing = headers[name] {
                guard name != "content-length" && name != "transfer-encoding" && name != "host" else {
                    throw bad("Ambiguous request framing")
                }
                headers[name] = existing + ", " + value
            } else { headers[name] = value }
        }
        let rawBody = Data(data[end.upperBound...])
        let body: Data
        if let encoding = headers["transfer-encoding"] {
            guard headers["content-length"] == nil, encoding.lowercased() == "chunked" else {
                throw bad("Unsupported request framing")
            }
            guard let decoded = try decodeChunks(rawBody, limit: maxBody) else { return nil }
            body = decoded
        } else {
            let length: Int
            if let value = headers["content-length"] {
                guard !value.isEmpty, value.allSatisfy({ $0 >= "0" && $0 <= "9" }),
                      let parsed = Int(value) else { throw bad("Invalid content length") }
                length = parsed
            } else { length = 0 }
            guard length <= maxBody else { throw HTTPError(status: 413, message: "Request body too large") }
            guard rawBody.count >= length else { return nil }
            body = Data(rawBody.prefix(length))
        }
        return Request(method: String(first[0]), target: String(first[1]), headers: headers, body: body)
    }

    private static func decodeChunks(_ data: Data, limit: Int) throws -> Data? {
        guard data.count <= limit + 64 * 1024 else { throw HTTPError(status: 413, message: "Request body too large") }
        var cursor = 0, body = Data()
        let crlf = Data("\r\n".utf8)
        while let line = data.range(of: crlf, in: cursor..<data.count) {
            let sizeText = String(decoding: data[cursor..<line.lowerBound], as: UTF8.self)
                .split(separator: ";", maxSplits: 1, omittingEmptySubsequences: false)[0]
            guard !sizeText.isEmpty, sizeText.allSatisfy({ $0.isHexDigit }),
                  let size = Int(sizeText, radix: 16) else {
                throw HTTPError(status: 400, message: "Invalid chunk size")
            }
            cursor = line.upperBound
            guard size <= limit - body.count else { throw HTTPError(status: 413, message: "Request body too large") }
            if size == 0 {
                // Consume optional trailers, which are not forwarded as headers.
                if data.count >= cursor + 2, data[cursor..<cursor + 2] == crlf { return body }
                if data.range(of: Data("\r\n\r\n".utf8), in: cursor..<data.count) != nil { return body }
                return nil
            }
            guard data.count - cursor >= size + 2 else { return nil }
            guard data[cursor + size..<cursor + size + 2] == crlf else {
                throw HTTPError(status: 400, message: "Invalid chunk terminator")
            }
            body.append(data[cursor..<cursor + size])
            cursor += size + 2
        }
        return nil
    }
}
