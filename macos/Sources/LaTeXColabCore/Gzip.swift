import Foundation
import Compression

public enum GzipError: Error, LocalizedError {
    case notGzip
    case corrupt
    public var errorDescription: String? {
        switch self {
        case .notGzip: return "Not a gzip stream"
        case .corrupt: return "Corrupt gzip stream"
        }
    }
}

/// Minimal gzip reader built on the Compression framework (raw DEFLATE after
/// the gzip header). Used for `.synctex.gz`.
public enum Gzip {
    public static func decompress(_ data: Data) throws -> Data {
        guard data.count >= 18, data[0] == 0x1f, data[1] == 0x8b, data[2] == 8 else {
            throw GzipError.notGzip
        }
        let flags = data[3]
        var idx = 10
        if flags & 0x04 != 0 { // FEXTRA
            guard idx + 2 <= data.count else { throw GzipError.corrupt }
            let xlen = Int(data[idx]) | (Int(data[idx + 1]) << 8)
            idx += 2 + xlen
        }
        if flags & 0x08 != 0 { // FNAME
            while idx < data.count && data[idx] != 0 { idx += 1 }
            idx += 1
        }
        if flags & 0x10 != 0 { // FCOMMENT
            while idx < data.count && data[idx] != 0 { idx += 1 }
            idx += 1
        }
        if flags & 0x02 != 0 { idx += 2 } // FHCRC
        guard idx < data.count - 8 else { throw GzipError.corrupt }
        return try inflate(data.subdata(in: idx..<(data.count - 8)))
    }

    static func inflate(_ input: Data) throws -> Data {
        let streamPtr = UnsafeMutablePointer<compression_stream>.allocate(capacity: 1)
        defer { streamPtr.deallocate() }
        var status = compression_stream_init(streamPtr, COMPRESSION_STREAM_DECODE, COMPRESSION_ZLIB)
        guard status != COMPRESSION_STATUS_ERROR else { throw GzipError.corrupt }
        defer { compression_stream_destroy(streamPtr) }

        let chunk = 256 * 1024
        let dst = UnsafeMutablePointer<UInt8>.allocate(capacity: chunk)
        defer { dst.deallocate() }
        var output = Data()

        try input.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
            guard let base = raw.bindMemory(to: UInt8.self).baseAddress else { throw GzipError.corrupt }
            streamPtr.pointee.src_ptr = base
            streamPtr.pointee.src_size = input.count
            repeat {
                streamPtr.pointee.dst_ptr = dst
                streamPtr.pointee.dst_size = chunk
                status = compression_stream_process(streamPtr, Int32(COMPRESSION_STREAM_FINALIZE.rawValue))
                let produced = chunk - streamPtr.pointee.dst_size
                if produced > 0 { output.append(dst, count: produced) }
            } while status == COMPRESSION_STATUS_OK
        }
        guard status == COMPRESSION_STATUS_END else { throw GzipError.corrupt }
        return output
    }

    /// Reads a text file that may or may not be gzip-compressed.
    public static func readTextFile(at url: URL) throws -> String {
        let data = try Data(contentsOf: url)
        let plain: Data
        if data.count > 2, data[0] == 0x1f, data[1] == 0x8b {
            plain = try decompress(data)
        } else {
            plain = data
        }
        if let s = String(data: plain, encoding: .utf8) { return s }
        return String(decoding: plain, as: UTF8.self)
    }
}
