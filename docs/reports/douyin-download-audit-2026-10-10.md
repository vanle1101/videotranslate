# Douyin download audit — máy B, 2026-10-10

## ROOT CAUSE

**Chưa xác minh được nguyên nhân gốc của lần tải chậm lịch sử.** Đã xác nhận chức năng tải hiện tại hoạt động nhanh trên máy B, kiểm tra các lỗi phần mềm có thể tái hiện và sửa chúng. Không có bằng chứng đủ để quy riêng lỗi lịch sử cho ISP, CDN hoặc máy tính.

Máy B được người dùng xác nhận dùng LAN. Video lấy từ source index và log là Douyin `7689326480735620402`: H.264, 3840×2160, 332,672993 giây, **951.798.675 byte = 907,7337 MiB**. Chưa có quyền truy cập máy A hoặc dữ liệu CDN/IP của lần đo trên máy A.

[Log baseline](</E:/Dịch video/workspace/audit/baseline_20261009_2308.json>) ghi lần tải ngày 09/10 từ 23:08:16 đến 23:47:14: **2.338 giây, 0,38824 MiB/s**. Metadata mất khoảng 5 giây; phần chậm nằm trong giai đoạn truyền dữ liệu. Log không có peer IP, DNS/TCP/TLS, tải CPU/disk hoặc retry chi tiết, nên không thể tái dựng đường truyền lúc đó.

Lần xác nhận độc lập bằng **cả worker và HTTP resolver nguyên trạng được lưu trước khi sửa** đạt **84,218 giây cho worker call**, file đúng SHA-256. [Bằng chứng và hash source](</E:/Dịch video/workspace/audit/frozen_baseline_verified.json>) xác nhận worker không chứa adaptive Range hoặc download_metrics và thực sự dùng resolver đóng băng. Bản sửa tải đầy đủ trong **85,750 giây**, cùng số byte và cùng SHA. Như vậy, phép đo không cho thấy bản sửa tạo ra cải thiện tốc độ đối với tuyến Douyin hiện đang nhanh.

Lần đo đầu `diagnose_douyin_20261010_000904.json` đã có telemetry mới. Giữ lại artifact, nhưng **không dùng nó làm baseline code cũ nguyên trạng**. Lần xác nhận có provenance ở trên thay thế nó trong bảng so sánh.

Các vấn đề phần mềm đã xác minh bằng source và kiểm thử:

- CLI pipeline gọi downloader đồng bộ ngay trong coroutine, có thể chặn event loop. Đường streaming của Studio đã chạy qua executor; không quy lỗi này cho lần tải lịch sử.
- Resolver chỉ thử địa chỉ DNS đầu tiên; địa chỉ lỗi không được thử sang public address kế tiếp trong cùng lần mở kết nối.
- Retry chưa xử lý đúng quyết định truy cập/rate limit: 403 bị thử lại; 429 chưa giữ đúng candidate và Retry-After, gồm dạng HTTP-date và thời gian chờ dài.
- Tốc độ hiển thị cũ là trung bình từ đầu lượt tải, chưa tách tốc độ hiện tại và trung bình; thiếu số kết nối/chiến lược/telemetry để điều tra.
- Công cụ benchmark ban đầu đếm request Range thành retry, tính SHA/server shutdown vào thời gian tải, bỏ sót thời gian socket timeout và chưa bảo đảm fixture interruption thực sự resume. Công cụ chẩn đoán trước sửa có thể dùng số byte yêu cầu làm throughput cho batch không hoàn chỉnh. Đã sửa và có test riêng.

## FIXES APPLIED

| File/module | Thay đổi và giới hạn |
| --- | --- |
| [core/pipeline.py](</E:/Dịch video/core/pipeline.py>) | Chạy download bằng asyncio.to_thread; truyền cancel_check để giữ phản hồi event loop và Stop. |
| [core/douyin_resolver.py](</E:/Dịch video/core/douyin_resolver.py>) | Thử các public DNS address trong ngân sách kết nối; giữ SNI/TLS verification và kiểm tra mọi redirect. Ghi host/IP/protocol/DNS/TCP/TLS/header timing; URL log chỉ còn scheme và hostname. Parse Retry-After numeric/date; không thử endpoint khác sau feed 403/429. |
| [core/download_ranges.py](</E:/Dịch video/core/download_ranges.py>) | Adaptive Range cho file lớn, chậm, có strong ETag. Đo prefix 4 MiB; chỉ tăng 2→4→8 khi gain ≥25%, giảm khi hiệu năng xấu đi; segment 4 MiB. Kiểm tra Content-Range/length/ETag/type/encoding toàn batch trước khi nối. Lỗi quay về owner retry/resume một luồng. |
| [core/download_worker.py](</E:/Dịch video/core/download_worker.py>) | Giữ streaming 256 KiB, checkpoint có SHA và atomic rename đang hoạt động tốt. Thêm tốc độ rolling 3 giây, average/ETA/strategy/connections/retry và network/disk metrics. 403 dừng; 429 giữ candidate và tôn trọng Retry-After. Chờ >60 giây được defer, giữ checkpoint, tránh vượt watchdog. Giữ thông tin resumable cả khi metadata bị hạn chế. |
| [core/downloader.py](</E:/Dịch video/core/downloader.py>) | Log thông số đo thật, endpoint host và peer IP; không log signed media URL. Phân biệt thời gian nhận header và block đầu tiên. |
| [static/app.js](</E:/Dịch video/static/app.js>) | Hiển thị tốc độ hiện tại/trung bình, chiến lược, số kết nối và retry từ worker; cập nhật polling khi các trường này đổi. Giữ phong cách và controls hiện có. |
| [scripts/benchmark_downloader.py](</E:/Dịch video/scripts/benchmark_downloader.py>) | Local HTTP server; file nhỏ/lớn, interruption→Range resume, timeout, 403/429/5xx, CDN failure, corrupted media và disk failure. Tách transfer, worker call, SHA và shutdown; dùng byte thực, đếm retry độc lập với request; assert hash và không có completed file ở failure. |
| [scripts/diagnose_douyin.py](</E:/Dịch video/scripts/diagnose_douyin.py>) | Resolve source mới mỗi lần; probe bounded, curl, IPv4/IPv6, concurrency 1/2/4/8. Chỉ so sánh Range chính xác và đầy đủ, ghi byte thực kể cả short read; dừng stage tiếp theo khi 403/429. Full benchmark không ghi đè artifact/checkpoint cũ. |

Không đổi HTTP client hay thêm pooling hàng loạt: một media stream đang đạt gần giới hạn Ethernet 100 Mbps; overhead mở kết nối không giải thích chênh lệch 20 lần. Không có bằng chứng chunk 256 KiB, CPU hoặc disk hiện tại tạo trần 0,4 MB/s. Không thay bản gốc bằng rendition thấp hơn. Extractor của bản gốc chỉ cung cấp một URL hợp lệ, nên không có tập mirror cùng chất lượng để làm CDN selection có căn cứ.

## BENCHMARK RESULTS

Các đồng hồ đo transfer đều monotonic. MiB/s dùng 1.048.576 byte; không đưa metadata extraction vào throughput.

### Cùng video thật trên máy B

| Metric | Baseline nguyên trạng, xác nhận lại | Optimized | Improvement |
| --- | ---: | ---: | --- |
| Average speed trong giai đoạn progress download | 10,867 MiB/s | 10,701 MiB/s | Không có tăng tốc đo được; −1,53% |
| Peak speed | 11,039 MiB/s, trung bình từ đầu lượt | 11,292 MiB/s, rolling 3 giây | Định nghĩa khác nhau; không so sánh |
| Download duration từ progress 0 đến đủ byte | 83,531 s | 84,828 s | Không có tăng tốc; +1,55% thời gian |
| Worker call, gồm mở kết nối và kiểm tra media | 84,218 s | 85,750 s | +1,82% thời gian; chưa đủ lượt để quy regression |
| Header response timing tại CDN cuối | Không có telemetry | 78 ms | Không có baseline tương đương |
| First completed 256 KiB block từ đầu worker transfer | Không đo | 562 ms | Không có baseline tương đương |
| Retry count | 0 | 0 | Giữ ổn định |
| Connections | 1 | 1 | Giữ một luồng khi nhanh |
| File integrity | SHA khớp | SHA khớp | Giữ nguyên dữ liệu |
| Download failures | 0/1 | 0/1 | Không lỗi trong hai lượt có provenance |

SHA-256 cả hai file và file nguồn lịch sử: `de3b924199ff464a6dff89da87388f1cae75b71c5c941b0a2763bfe37d04ca85`.

[Bản sửa tải đầy đủ](</E:/Dịch video/workspace/audit/optimized_live_audit/diagnose_douyin_20261010_002039.json>) đo network read wait **82,740 s**, disk write/checkpoint **1,604 s**, DNS **156 ms**, TCP **16 ms**, TLS **31 ms**. CDN cuối `v5-hl-mly-ov.zjcdn.com`, IPv6 `2405:4800:10a::7645:11b0`, HTTP/1.1, HTTP 200. `first_byte_ms` trong artifact cũ thực chất là thời gian hoàn thành block đầu sau header; code mới đặt tên rõ `first_chunk_ms` và giữ alias tương thích. Header timing gồm TCP/TLS của request cuối, không cộng lặp nó với connect time.

Ethernet đang negotiated **100 Mbps**; cận lý thuyết cho 951.798.675 byte là khoảng **76,14 s**, chưa tính overhead. Thời gian 84–86 s phù hợp khả năng link hiện tại. Sampler ở baseline nguyên trạng: RSS peak **42,98 MiB**, CPU trung bình **13,68% theo thang process psutil** (100% = một logical core). Mẫu gồm metadata và SHA/ffprobe, không thể dùng để suy ra tải CPU của hôm trước. Không tắt antivirus hoặc thay cấu hình mạng.

### Probe mạng, dữ liệu giới hạn

[Probe mới sau sửa công cụ](</E:/Dịch video/workspace/audit/final_network_verified/diagnose_douyin_20261010_101642.json>): tất cả Range đều HTTP 206, đúng 8 MiB, Content-Range/total/ETag hợp lệ; Accept-Ranges=bytes.

| Phép đo | MiB/s | Cách tính |
| --- | ---: | --- |
| Core single | 10,352 | Byte/thời gian đọc body |
| curl | 6,712 | Byte/thời gian request gồm kết nối |
| Range concurrency 1 / 2 / 4 / 8 | 7,159 / 6,228 / 6,792 / 6,680 | Cùng tổng 8 MiB, gồm mở kết nối |
| IPv4 / IPv6 core | 11,283 / 10,590 | Thời gian body; IP `183.80.211.162` / `2405:4800:10a::7645:11b0` |
| File thường, requests / curl | 4,560 / 3,259 | Reference 8 MiB speed.cloudflare.com; thời điểm khác |

[Ba probe trước đó](</E:/Dịch video/workspace/audit/diagnose_douyin_20261010_001841.json>) đạt core 10,547/10,523/10,433 MiB/s; IPv4 11,289/11,268/11,283, IPv6 10,560/9,813/10,442. Một probe sớm core chỉ 2,037 MiB/s trong khi requests đạt 10,505; các request khác thời điểm/IP và có overhead khác nhau, nên **không coi đó là bằng chứng client này nhanh hơn client khác**.

Parallel không có lợi trên các probe Douyin này; không bật nhiều kết nối cho full download đang nhanh. IPv4 hơi nhanh hơn trong mẫu hiện tại, nhưng không giải thích 20 lần và chưa có lý do ép IPv4 cố định. Không có proxy HTTP(S) trong môi trường đo; adapter HotspotShield đang disconnected. curl 8.13.0 và stdlib client hiện không có HTTP/2, nên phép đối chiếu HTTP/2 được ghi là unavailable.

### Cải thiện có thể tái hiện trong local server

Cùng file MP4 96 MiB, server trì hoãn 20 ms mỗi 64 KiB **trên từng kết nối**; hai lượt mỗi chế độ, không chạy đồng thời hai benchmark.

| Metric | Single | Adaptive | Improvement |
| --- | ---: | ---: | ---: |
| Mean transfer duration | 31,727 s | 17,164 s | Giảm 45,90% |
| Mean transfer speed | 3,026 MiB/s | 5,593 MiB/s | 1,848× |
| Retry count | 0/0 | 0/0 | Giữ ổn định |
| Peak concurrent connections | 1/1 | 2/2 | Chọn theo đo đạc |
| Integrity | 2/2 đúng SHA | 2/2 đúng SHA | Không mất byte |

Evidence: [single](</E:/Dịch video/workspace/audit/range_single_verified.json>), [adaptive](</E:/Dịch video/workspace/audit/range_adaptive_verified.json>). Adaptive có 24 request thành công mỗi lượt; đó **không phải 23 retry**. Tốc độ cuối và số kết nối cuối có thể giảm khi còn tail nhỏ; báo cáo ghi cả peak và lịch sử chiến lược. Kết quả này chứng minh lợi ích ở bottleneck giả lập theo kết nối, **không chứng minh Douyin đang throttle theo kết nối**.

## TEST RESULTS

| Phạm vi đã chạy | Passed | Failed / Skipped |
| --- | ---: | --- |
| Downloader/resolver/Range/async pipeline/diagnostics/benchmark accounting | 257 | 0 / 0 |
| URL progress/pipeline/backend loop/runtime preflight/errors | 67 | 0 / 0 |
| Node UI interactions | 204 | 0 / 0 |
| Retest sau đổi nhãn first-chunk telemetry | 135, là subset ở trên | 0 / 0 |
| Local acceptance benchmark cuối | 10/10 scenario đúng kỳ vọng | Các HTTP/disk/media fault phải fail download an toàn |
| py_compile các module/script thay đổi; git diff --check | Đạt | Không có build bundle cần chạy cho thay đổi Python/JS này |
| Real video | ffprobe và SHA đạt; FFmpeg decode toàn video/audio exit 0; audio prep WAV 2 giây exit 0 | Không chạy lại toàn bộ ASR/translation/TTS trả phí |
| Test ASR CPU được retest riêng | 0 | 1 failed: config int8_float32, test hardcode int8 |

XML/log ở [final_regression](</E:/Dịch video/workspace/audit/final_regression>). [Local acceptance](</E:/Dịch video/workspace/audit/offline_acceptance_final.json>) xác nhận interruption có **1 request Range, 1 retry, đúng hash**; timeout đo **18 s socket wait**, dừng sau 5 retry; 403 chỉ 1 request; 429/500/503 retry hữu hạn. Dữ liệu hỏng và disk full không xuất bản completed file.

Tests cũng bao phủ checkpoint bị sửa hash/size/stale, nguồn đổi ETag, Range bị bỏ qua, Content-Range sai, short/corrupt chunk, batch lỗi→single resume, Stop giữ contiguous prefix, numeric/date/long Retry-After, và metadata 403/429 giữ trạng thái resumable. Các fault này chỉ phát sinh trên mocks/local server.

Regression rộng đã có lần thất bại ngoài downloader, cùng lỗi collection khi quét cả vendored engines và backup workspace. Một test dub từng thất bại đã retest đạt; không khẳng định toàn bộ suite hiện đã PASS. Giữ nguyên config ASR và những thay đổi ngoài phạm vi downloader đang có trong workspace.

## REMAINING BOTTLENECKS

- Không tái hiện được mức 0,4 MB/s trong lần tải đầy đủ bằng source nguyên trạng. Không biết endpoint/IP, packet loss, routing hoặc tải máy của lần tải 38m58s; không thể phân biệt ISP/routing, congestion, CDN throttling hay tải hệ thống lịch sử.
- Không có quyền truy cập máy A. Việc khác Wi-Fi/LAN đồng thời khác mạng không đủ để kết luận loại kết nối gây lỗi.
- Hiện LAN 100 Mbps là giới hạn đo được; không tự thay adapter, router hoặc cấu hình hệ thống.
- CDN không cung cấp checksum tin cậy cho mọi byte. Strong ETag/TLS, kiểm tra range/length, ffprobe và hash so với file nguồn đã chứng minh integrity cho workload này; không thể phát hiện mọi nội dung bị CDN thay đổi cùng length/ETag mà không có checksum nguồn. Bài test corruption cùng độ dài ghi rõ giới hạn này.
- Source không có nhiều original CDN candidate để benchmark fallback cùng chất lượng. Không thay rendition hoặc né 403/429.
- Baseline đầy đủ và bản sửa được đo tuần tự ở các thời điểm khác nhau; chênh lệch khoảng 1–2% không đủ dữ liệu để quy regression hoặc improvement. Các sửa sau full download chủ yếu ở exception paths, giới hạn buffer và nhãn telemetry; đã kiểm thử fault/local benchmark và probe source mới, không tải lại 908 MiB sau từng đổi nhãn.
- Chưa chạy toàn bộ pipeline AI end-to-end sau sửa downloader; đã kiểm tra media decode/audio prep và integration tests. Test ASR config nêu trên ngăn việc báo all regression PASS.

## FINAL STATUS

**BLOCKED — INSUFFICIENT ACCESS/EVIDENCE**, áp dụng cho kết luận nguyên nhân của lần tải 39 phút và so sánh A/B. Phần sửa downloader đã chạy thật, test và benchmark đã ghi bằng chứng; tải hiện tại trên B hoàn tất khoảng 85 giây, file nguyên vẹn. Không báo VERIFIED FIXED cho hiện tượng lịch sử, không quy chắc chắn cho ISP/CDN, không nhận công cải thiện 27 lần cho code.

Có thể chạy lại suite bằng venv Python với `scripts/benchmark_downloader.py --scenario all`; chẩn đoán thật bằng `scripts/diagnose_douyin.py` luôn resolve metadata mới. Đây là khả năng tái lập của artifact; agent đã thực hiện các phép thử được báo ở trên, không yêu cầu người dùng tự chạy để hoàn tất phần đã được xác minh.
