// Låtsade delar av ESPHome, så att LD2461-komponenten kan byggas och provas
// på en vanlig dator (se scripts/tests/test_ld2461_frames.py). Bara det som
// komponenten använder finns med.
#pragma once

#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <deque>
#include <functional>
#include <string>
#include <sys/types.h>
#include <vector>

#define ESP_LOGD(...) ((void) 0)
#define ESP_LOGCONFIG(...) ((void) 0)

#define SUB_SENSOR(name) \
 protected: \
  sensor::Sensor *name##_sensor_{nullptr}; \
\
 public: \
  void set_##name##_sensor(sensor::Sensor *s) { this->name##_sensor_ = s; }
#define SUB_BINARY_SENSOR(name) \
 protected: \
  binary_sensor::BinarySensor *name##_binary_sensor_{nullptr}; \
\
 public: \
  void set_##name##_binary_sensor(binary_sensor::BinarySensor *s) { this->name##_binary_sensor_ = s; }
#define SUB_TEXT_SENSOR(name) \
 protected: \
  text_sensor::TextSensor *name##_text_sensor_{nullptr}; \
\
 public: \
  void set_##name##_text_sensor(text_sensor::TextSensor *s) { this->name##_text_sensor_ = s; }

namespace esphome {

inline uint32_t millis() { return 0; }

inline std::string str_snprintf(const char *fmt, size_t len, ...) {
  std::string out(len, '\0');
  va_list args;
  va_start(args, len);
  int written = vsnprintf(&out[0], len + 1, fmt, args);
  va_end(args);
  out.resize(written < 0 ? 0 : std::min(static_cast<size_t>(written), len));
  return out;
}

class PollingComponent {
 public:
  virtual ~PollingComponent() = default;
  virtual void setup() {}
  virtual void loop() {}
  virtual void update() {}
  virtual void dump_config() {}
  void set_timeout(uint32_t, std::function<void()>) {}
};

namespace uart {
/** Det radarn skickar läggs i `rx`, och det komponenten skickar hamnar i `tx`. */
class UARTDevice {
 public:
  std::deque<uint8_t> rx;
  std::vector<uint8_t> tx;
  int available() { return static_cast<int>(rx.size()); }
  uint8_t read() {
    uint8_t byte = rx.front();
    rx.pop_front();
    return byte;
  }
  void write_byte(uint8_t byte) { tx.push_back(byte); }
  void write_array(const uint8_t *data, size_t len) { tx.insert(tx.end(), data, data + len); }
};
}  // namespace uart

namespace sensor {
class Sensor {
 public:
  float state{0};
  int published{0};
  void publish_state(float value) {
    state = value;
    published++;
  }
};
}  // namespace sensor

namespace binary_sensor {
class BinarySensor {
 public:
  bool state{false};
  void publish_state(bool value) { state = value; }
};
}  // namespace binary_sensor

namespace text_sensor {
class TextSensor {
 public:
  std::string state;
  void publish_state(const std::string &value) { state = value; }
};
}  // namespace text_sensor

}  // namespace esphome
