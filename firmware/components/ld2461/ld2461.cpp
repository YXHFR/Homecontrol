// Changed for Homecontrol (https://github.com/YXHFR/Homecontrol): the
// coordinates are now published in mm, also when nobody is seen, the frame
// bytes are read unsigned, frames are read only within what was received,
// the number code is built only with USE_LD2461_NUMBER, and the firmware
// version is formatted safely. Every change is marked "Homecontrol:". See
// README.md in this folder.
#include "ld2461.h"

#include <algorithm>

namespace esphome {
namespace ld2461 {

static const char *TAG = "ld2461";

#define PERSON_PUBLISH_(name, person, val) \
    sensor::Sensor *name##_##person; \
    name##_##person = this->name##_##person##_sensor_; \
    if (name##_##person != nullptr) name##_##person->publish_state(val);
#define PERSON_PUBLISH(name, person, val) PERSON_PUBLISH_(name, person, val)

#define highbyte(val) (uint8_t)((val) >> 8)
#define lowbyte(val) (uint8_t)((val) &0xff)

void LD2461::setup() {
    this->set_timeout(1000, [this]() { this->read_all_info(); });
}

void LD2461::update() {
    report_position();

#ifdef USE_TEXT_SENSOR
    if (this->uuid_text_sensor_ != nullptr && this->uuid_text_sensor_->state != this->uuid_) {
        this->uuid_text_sensor_->publish_state(this->uuid_);
    }

    if (this->version_text_sensor_ != nullptr && this->version_text_sensor_->state != this->version_) {
        this->version_text_sensor_->publish_state(this->version_);
    }
#endif

}

void LD2461::loop() {
    while (available()) {
        // Homecontrol: unsigned, so that 0xDD and 0xFF match the cases below
        // also where char is signed.
        uint8_t c = read();
        // Homecontrol: start over instead of letting size wrap around.
        if(serial_data.size == UINT8_MAX) {
            serial_data.size = 0;
            serial_data.frame_start = 0;
            frame_header_seen_ = false;
        }
        serial_data.buffer[serial_data.size] = c;
        serial_data.size ++;

        switch(c) {
            case FH_LAST: {
                uint8_t fh_size = sizeof(FRAME_HEADER);
                if(serial_data.size >= fh_size && memcmp(serial_data.buffer+serial_data.size-fh_size, FRAME_HEADER, fh_size) == 0) {
                    serial_data.frame_start = serial_data.size - fh_size;
                    frame_header_seen_ = true;
                }
                break;
            }
            case FE_LAST: {
                uint8_t fe_size = sizeof(FRAME_END);
                uint8_t min_packet_size = sizeof(FRAME_HEADER) + fe_size + 5;

                if(serial_data.size >= min_packet_size && memcmp(serial_data.buffer+serial_data.size-fe_size, FRAME_END, fe_size) == 0) {
                    uint8_t frame_size = sizeof(response_frame_header);
                    // Homecontrol: read only what has been received since the frame header.
                    uint8_t received = serial_data.size - serial_data.frame_start;
                    if(!frame_header_seen_ || serial_data.frame_start > serial_data.size || received < frame_size) {
                        serial_data.size = 0;
                        serial_data.frame_start = 0;
                        frame_header_seen_ = false;
                        break;
                    }
                    memcpy(&response_frame_header, serial_data.buffer+serial_data.frame_start, frame_size);

                    // Homecontrol: a coordinate frame has no command value; its first
                    // byte is the first target's X, which is 0 when nobody is seen.
                    if(response_frame_header.command_val || response_frame_header.command == GET_COORDINATES) {
                        if(response_frame_header.command == GET_REGIONS) {
                            uint8_t sensor_regions_size = sizeof(sensor_regions);
                            if(received >= frame_size + sensor_regions_size) {
                                memcpy(&sensor_regions, serial_data.buffer+serial_data.frame_start+frame_size, sensor_regions_size);
                            }
                        } else if(response_frame_header.command == GET_COORDINATES){
                            uint8_t packet_size = sizeof(data_packet_struct); 
                            // Homecontrol: only a frame of exactly the expected size, from its
                            // header, is read. The coordinates are handed on to the sensors
                            // (upstream never set person[]).
                            if(received == packet_size) {
                                memcpy(&received_data, serial_data.buffer+serial_data.frame_start, packet_size);
                                for(int i=0; i<5; i++) {
                                    person[i].x = to_mm(received_data.person[i].cx);
                                    person[i].y = to_mm(received_data.person[i].cy);
                                }
                                new_coordinates_ = true;
                            }
                        }
                          else {
                            uint8_t length = std::min<uint8_t>(response_frame_header.size_0, received - frame_size);
                            memcpy(response_buffer, serial_data.buffer+serial_data.frame_start+frame_size, length);
                        }

                        switch(response_frame_header.command) {

                            case READ_FIRMWARE:
                                // Homecontrol: four values for four fields (upstream had five fields).
                                this->version_ = str_snprintf("V%02X.%02X.%02X%02X", 17, response_buffer[2], 
                                    response_buffer[3], response_buffer[1], response_buffer[0]).c_str();
                                this->uuid_ = str_snprintf("%02x%02x%02x%02x", 17, response_buffer[4], 
                                    response_buffer[5], response_buffer[6], response_buffer[7]).c_str();
                                break;

#ifdef USE_LD2461_NUMBER
                            case GET_REGIONS:
                                for(int i=0; i<3; i++) {
                                    number::Number *x0 = this->region_numbers_[i][0];
                                    number::Number *y0 = this->region_numbers_[i][1];
                                    number::Number *x1 = this->region_numbers_[i][2];
                                    number::Number *y1 = this->region_numbers_[i][3];

                                    if(x0 != nullptr) x0->publish_state(sensor_regions.coordinates[i].X0/10);
                                    if(y0 != nullptr) y0->publish_state(sensor_regions.coordinates[i].Y0/10);
                                    if(x1 != nullptr) x1->publish_state(sensor_regions.coordinates[i].X1/10);
                                    if(y1 != nullptr) y1->publish_state(sensor_regions.coordinates[i].Y1/10);
                                }
                                break;
#endif
                        }
                        serial_data.size = 0;
                        serial_data.frame_start = 0;
                        frame_header_seen_ = false;
                    } else {
                        ESP_LOGD(TAG, "ACK Error, command %02x", response_frame_header.command);
                    }
                }
                break;
            }
        }

    }
}

void LD2461::dump_config(){
    ESP_LOGCONFIG(TAG, "LD2461:");
    ESP_LOGCONFIG(TAG, "  UID : %s", const_cast<char *>(this->uuid_.c_str()));
    ESP_LOGCONFIG(TAG, "  Firmware Version : %s", const_cast<char *>(this->version_.c_str()));
}

void LD2461::restore_factory() {
    this->send_command_(RESTORE_FACTORY, nullptr, 0);
    this->set_timeout(1000, [this]() { this->read_all_info(); });
}

void LD2461::set_baud_rate(uint8_t state) {
    uint8_t cmd_value[2] = {state, 0x00};
    this->send_command_(SET_BAUDRATE, cmd_value, 2);
}

void LD2461::set_regions_type(uint8_t state) {
    uint8_t sensor_regions_size = sizeof(sensor_regions);
    sensor_regions.type = state;
    this->send_command_(SET_REGIONS, (uint8_t *)&sensor_regions, sensor_regions_size);
}

void LD2461::set_reporting_type(uint8_t state) {
    uint8_t sensor_reporting_size = sizeof(sensor_reporting);
    sensor_reporting.type = state;
    this->send_command_(SET_REPORTING, (uint8_t *)&sensor_reporting, sensor_reporting_size);
}

#ifdef USE_LD2461_NUMBER
void LD2461::set_region_number(int region, int coord, number::Number *n) {
    this->region_numbers_[region][coord] = n;
}
#endif

#ifdef USE_LD2461_NUMBER
void LD2461::set_region(uint8_t region) {
    number::Number *x0 = this->region_numbers_[region][0];
    number::Number *y0 = this->region_numbers_[region][1];
    number::Number *x1 = this->region_numbers_[region][2];
    number::Number *y1 = this->region_numbers_[region][3];

    if (x0->has_state()) sensor_regions.coordinates[region].X0 = x0->state*10;
    if (y0->has_state()) sensor_regions.coordinates[region].Y0 = y0->state*10;
    if (x1->has_state()) sensor_regions.coordinates[region].X1 = x1->state*10;
    if (y1->has_state()) sensor_regions.coordinates[region].Y1 = y1->state*10;
}
#endif

// Private Methods //////////////////////////////////////////////////////////////

void LD2461::report_position(void) {

#ifdef USE_SENSOR
    for(int i=0; i<5; i++) {
        if(person[i].x != 0 && person[i].y != 0) {
            person_before[i].x = person[i].x;
            person_before[i].y = person[i].y;
        }
    }

    PERSON_PUBLISH(position_x, 0, person[0].x);
    PERSON_PUBLISH(position_y, 0, person[0].y);

    PERSON_PUBLISH(position_x, 1, person[1].x);
    PERSON_PUBLISH(position_y, 1, person[1].y);

    PERSON_PUBLISH(position_x, 2, person[2].x);
    PERSON_PUBLISH(position_y, 2, person[2].y);

    PERSON_PUBLISH(position_x, 3, person[3].x);
    PERSON_PUBLISH(position_y, 3, person[3].y);

    PERSON_PUBLISH(position_x, 4, person[4].x);
    PERSON_PUBLISH(position_y, 4, person[4].y);
    

    if (this->target_count_sensor_ != nullptr) {
        uint8_t target_count = 0;
        for(int i=0; i<5; i++) {
            if(received_data.person[i].cx != 0) target_count++;
        }

        this->target_count_sensor_->publish_state(target_count);
    }
#endif

#ifdef USE_BINARY_SENSOR
    {
        int32_t current_millis = millis();

    #ifdef USE_LD2461_NUMBER
        for (auto *presence_region : presence_regions) {
            presence_region->check_target(person);
        }
    #endif

        for(int i=0; i<5; i++) {
            bool exiting=false;

    #ifdef USE_LD2461_NUMBER
            for (auto *entry_point : entry_points) {
                if(entry_point->check_point(person_before[i])) {
                    exiting = true;
                    break;
                }
            }
    #endif

            if(received_data.person[i].cx) {
                if(exiting) presence_millis[i] = 0;
                else presence_millis[i] = current_millis + presence_timeout*1000;
            }
        }

    bool target = (
        (received_data.person[0].cx || presence_millis[0] > current_millis) ||
        (received_data.person[1].cx || presence_millis[1] > current_millis) ||
        (received_data.person[2].cx || presence_millis[2] > current_millis) ||
        (received_data.person[3].cx || presence_millis[3] > current_millis) ||
        (received_data.person[4].cx || presence_millis[4] > current_millis)
    );

    if (this->target_binary_sensor_ != nullptr) {
        this->target_binary_sensor_->publish_state(target);
    }
}

#endif
}
// Homecontrol: whether a coordinate frame has been read since the last call.
bool LD2461::take_new_coordinates() {
    bool seen = new_coordinates_;
    new_coordinates_ = false;
    return seen;
}

// Homecontrol: each coordinate is read as one signed byte in 0.1 m, as the
// frame layout in typedefs.h has it. Not tried on a real radar.
double LD2461::to_mm(uint8_t data) {
    return static_cast<int8_t>(data) * 100.0;
}

int16_t LD2461::transform(uint16_t data) {
    return (data>>15) == 1 ? -1 * (data&0x7FFF) : data&0x7FFF;
}

void LD2461::add_entry_point(EntryPoint *entry_point) { entry_points.emplace_back(entry_point); }

void LD2461::add_presence_region(PresenceRegion *presence_region) { presence_regions.emplace_back(presence_region); }

#ifdef USE_LD2461_NUMBER
void LD2461::set_presence_timeout_number() {
    if (this->presence_timeout_number_ != nullptr && this->presence_timeout_number_->has_state()) {
        presence_timeout = this->presence_timeout_number_->state;
    }
}
#endif

void LD2461::send_command_(uint8_t command, const uint8_t *command_value, int command_value_len) {
    this->write_array(FRAME_HEADER, 3);

    u_int8_t check_sum = command;
    
    int len = 2;
    
    if (command_value != nullptr) {
        len += command_value_len;
        check_sum += *command_value;
    }

    check_sum = lowbyte(check_sum);

    this->write_byte(highbyte(len));
    this->write_byte(lowbyte(len));

    this->write_byte(command);

    if (command_value != nullptr) {
        for (int i = 0; i < command_value_len; i++) {
            this->write_byte(command_value[i]);
        }
    }

    this->write_array(&check_sum, 1);
    this->write_array(FRAME_END, 3);
}

void LD2461::read_all_info() {
    this->get_version_();
    this->get_reporting_();
#ifdef USE_SELECT
    this->get_regions_();
#endif

}

void LD2461::get_version_() { this->send_command_(READ_FIRMWARE, nullptr, 0); }

void LD2461::get_regions_() { this->send_command_(GET_REGIONS, nullptr, 0); }

void LD2461::get_reporting_() { this->send_command_(GET_REPORTING, nullptr, 0); }

}  // namespace ld2461
}  // namespace esphome

