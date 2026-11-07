#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <linux/gpio.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#ifndef GPIO_V2_LINE_FLAG_BIAS_PULL_UP
#define GPIO_V2_LINE_FLAG_BIAS_PULL_UP 0
#endif

#define DEFAULT_CHIP "/dev/gpiochip0"
#define DEFAULT_GPIO 92
#define SENSOR_NAME "DHT11"

enum read_status {
    READ_OK = 0,
    ERR_SENSOR_NO_RESPONSE_LOW,
    ERR_SENSOR_NO_RESPONSE_HIGH,
    ERR_BIT_START_TIMEOUT,
    ERR_BIT_VALUE_TIMEOUT,
    ERR_CHECKSUM,
    ERR_RANGE,
    ERR_GPIO,
    ERR_ARGS
};

struct options {
    const char *chip;
    unsigned int gpio;
    int pull_up;
    int threshold_us;
    int debug;
};

struct reading {
    unsigned char data[5];
    unsigned int high_us[40];
    double humidity;
    double temperature;
    enum read_status status;
    const char *error;
};

static const char *status_name(enum read_status status)
{
    switch (status) {
    case READ_OK:
        return "OK";
    case ERR_SENSOR_NO_RESPONSE_LOW:
        return "ERR_SENSOR_NO_RESPONSE_LOW";
    case ERR_SENSOR_NO_RESPONSE_HIGH:
        return "ERR_SENSOR_NO_RESPONSE_HIGH";
    case ERR_BIT_START_TIMEOUT:
        return "ERR_BIT_START_TIMEOUT";
    case ERR_BIT_VALUE_TIMEOUT:
        return "ERR_BIT_VALUE_TIMEOUT";
    case ERR_CHECKSUM:
        return "ERR_CHECKSUM";
    case ERR_RANGE:
        return "ERR_RANGE";
    case ERR_GPIO:
        return "ERR_GPIO";
    case ERR_ARGS:
        return "ERR_ARGS";
    default:
        return "ERR_UNKNOWN";
    }
}

static uint64_t monotonic_us(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC_RAW, &ts);
    return (uint64_t)ts.tv_sec * 1000000ULL + (uint64_t)ts.tv_nsec / 1000ULL;
}

static void busy_wait_us(unsigned int usec)
{
    uint64_t deadline = monotonic_us() + usec;
    while (monotonic_us() < deadline) {
    }
}

static void sleep_us(unsigned int usec)
{
    struct timespec ts;
    ts.tv_sec = usec / 1000000U;
    ts.tv_nsec = (long)(usec % 1000000U) * 1000L;
    while (nanosleep(&ts, &ts) != 0 && errno == EINTR) {
    }
}

static void best_effort_realtime(void)
{
    struct sched_param param;
    memset(&param, 0, sizeof(param));
    param.sched_priority = 20;
    sched_setscheduler(0, SCHED_FIFO, &param);
    mlockall(MCL_CURRENT | MCL_FUTURE);
}

static int set_line_value(int line_fd, int value)
{
    struct gpio_v2_line_values values;
    memset(&values, 0, sizeof(values));
    values.mask = 1;
    values.bits = value ? 1 : 0;
    return ioctl(line_fd, GPIO_V2_LINE_SET_VALUES_IOCTL, &values);
}

static int get_line_value(int line_fd)
{
    struct gpio_v2_line_values values;
    memset(&values, 0, sizeof(values));
    values.mask = 1;
    if (ioctl(line_fd, GPIO_V2_LINE_GET_VALUES_IOCTL, &values) < 0) {
        return -1;
    }
    return (values.bits & 1) ? 1 : 0;
}

static int wait_for_level(int line_fd, int expected, unsigned int timeout_us)
{
    uint64_t deadline = monotonic_us() + timeout_us;
    while (monotonic_us() <= deadline) {
        int value = get_line_value(line_fd);
        if (value < 0) {
            return -1;
        }
        if (value == expected) {
            return 1;
        }
    }
    return 0;
}

static int request_line(const struct options *opts, int *chip_fd, int *line_fd, char *errbuf, size_t errbuf_len)
{
    struct gpio_v2_line_request req;
    int fd = open(opts->chip, O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        snprintf(errbuf, errbuf_len, "open %s failed: %s", opts->chip, strerror(errno));
        return -1;
    }

    memset(&req, 0, sizeof(req));
    req.offsets[0] = opts->gpio;
    req.num_lines = 1;
    req.event_buffer_size = 0;
    snprintf(req.consumer, sizeof(req.consumer), "temperature-humidity-c");
    req.config.flags = GPIO_V2_LINE_FLAG_OUTPUT | GPIO_V2_LINE_FLAG_OPEN_DRAIN;
    if (opts->pull_up) {
        req.config.flags |= GPIO_V2_LINE_FLAG_BIAS_PULL_UP;
    }
    req.config.num_attrs = 1;
    req.config.attrs[0].attr.id = GPIO_V2_LINE_ATTR_ID_OUTPUT_VALUES;
    req.config.attrs[0].attr.values = 1;
    req.config.attrs[0].mask = 1;

    if (ioctl(fd, GPIO_V2_GET_LINE_IOCTL, &req) < 0) {
        snprintf(errbuf, errbuf_len, "request GPIO%u on %s failed: %s", opts->gpio, opts->chip, strerror(errno));
        close(fd);
        return -1;
    }

    *chip_fd = fd;
    *line_fd = req.fd;
    return 0;
}

static enum read_status read_sensor(const struct options *opts, struct reading *out, char *errbuf, size_t errbuf_len)
{
    int chip_fd = -1;
    int line_fd = -1;
    memset(out, 0, sizeof(*out));
    out->status = ERR_GPIO;

    if (request_line(opts, &chip_fd, &line_fd, errbuf, errbuf_len) < 0) {
        out->error = errbuf;
        return ERR_GPIO;
    }

    set_line_value(line_fd, 1);
    sleep_us(1000);
    if (set_line_value(line_fd, 0) < 0) {
        snprintf(errbuf, errbuf_len, "drive GPIO%u low failed: %s", opts->gpio, strerror(errno));
        out->error = errbuf;
        goto gpio_error;
    }
    /* DHT11 requires the host to hold DATA low for at least 18 ms. */
    sleep_us(20000);
    if (set_line_value(line_fd, 1) < 0) {
        snprintf(errbuf, errbuf_len, "release GPIO%u failed: %s", opts->gpio, strerror(errno));
        out->error = errbuf;
        goto gpio_error;
    }
    busy_wait_us(30);

    int wait_result = wait_for_level(line_fd, 0, 200);
    if (wait_result <= 0) {
        out->status = wait_result < 0 ? ERR_GPIO : ERR_SENSOR_NO_RESPONSE_LOW;
        goto done;
    }
    wait_result = wait_for_level(line_fd, 1, 200);
    if (wait_result <= 0) {
        out->status = wait_result < 0 ? ERR_GPIO : ERR_SENSOR_NO_RESPONSE_HIGH;
        goto done;
    }
    wait_result = wait_for_level(line_fd, 0, 200);
    if (wait_result <= 0) {
        out->status = wait_result < 0 ? ERR_GPIO : ERR_SENSOR_NO_RESPONSE_LOW;
        goto done;
    }

    for (int bit_index = 0; bit_index < 40; bit_index++) {
        wait_result = wait_for_level(line_fd, 1, 200);
        if (wait_result <= 0) {
            out->status = wait_result < 0 ? ERR_GPIO : ERR_BIT_START_TIMEOUT;
            goto done;
        }

        uint64_t high_start = monotonic_us();
        wait_result = wait_for_level(line_fd, 0, 200);
        if (wait_result <= 0) {
            out->status = wait_result < 0 ? ERR_GPIO : ERR_BIT_VALUE_TIMEOUT;
            goto done;
        }
        unsigned int high_time = (unsigned int)(monotonic_us() - high_start);
        out->high_us[bit_index] = high_time;
        out->data[bit_index / 8] <<= 1;
        if (high_time > (unsigned int)opts->threshold_us) {
            out->data[bit_index / 8] |= 1;
        }
    }

    unsigned char checksum = (unsigned char)(out->data[0] + out->data[1] + out->data[2] + out->data[3]);
    if (checksum != out->data[4]) {
        out->status = ERR_CHECKSUM;
        goto done;
    }

    if (out->data[1] > 9 || out->data[3] > 9) {
        out->status = ERR_RANGE;
        goto done;
    }

    out->humidity = out->data[0] + out->data[1] / 10.0;
    out->temperature = (out->data[2] & 0x7F) + out->data[3] / 10.0;
    if (out->data[2] & 0x80) {
        out->temperature = -out->temperature;
    }

    if (out->humidity < 20.0 || out->humidity > 90.0 || out->temperature < 0.0 || out->temperature > 50.0) {
        out->status = ERR_RANGE;
        goto done;
    }

    out->status = READ_OK;
    goto done;

gpio_error:
    out->status = ERR_GPIO;

done:
    if (line_fd >= 0) {
        close(line_fd);
    }
    if (chip_fd >= 0) {
        close(chip_fd);
    }
    return out->status;
}

static void print_json(const struct options *opts, const struct reading *reading)
{
    printf("{");
    printf("\"ok\":%s,", reading->status == READ_OK ? "true" : "false");
    printf("\"sensor\":\"%s\",", SENSOR_NAME);
    printf("\"source\":\"c-gpio-v2\",");
    printf("\"chip\":\"%s\",", opts->chip);
    printf("\"gpio\":%u,", opts->gpio);
    printf("\"status\":\"%s\",", status_name(reading->status));
    printf("\"temperature_c\":%.1f,", reading->temperature);
    printf("\"humidity_percent\":%.1f,", reading->humidity);
    printf("\"raw\":[%u,%u,%u,%u,%u]", reading->data[0], reading->data[1], reading->data[2], reading->data[3], reading->data[4]);
    if (reading->error) {
        printf(",\"error\":\"%s\"", reading->error);
    }
    if (opts->debug || reading->status != READ_OK) {
        printf(",\"high_us\":[");
        for (int i = 0; i < 40; i++) {
            if (i) {
                printf(",");
            }
            printf("%u", reading->high_us[i]);
        }
        printf("]");
    }
    printf("}\n");
}

static void usage(const char *argv0)
{
    fprintf(stderr, "Usage: %s [--chip /dev/gpiochip0|gpiochip0] [--gpio 92] [--pull-up|--no-pull-up] [--threshold-us 50] [--debug]\n", argv0);
}

static const char *normalize_chip(const char *value, char *buffer, size_t buffer_len)
{
    if (strncmp(value, "/dev/", 5) == 0) {
        return value;
    }
    snprintf(buffer, buffer_len, "/dev/%s", value);
    return buffer;
}

int main(int argc, char **argv)
{
    struct options opts;
    char chip_buffer[128];
    char errbuf[256];
    const char *chip_arg = DEFAULT_CHIP;
    memset(&opts, 0, sizeof(opts));
    memset(errbuf, 0, sizeof(errbuf));
    opts.chip = DEFAULT_CHIP;
    opts.gpio = DEFAULT_GPIO;
    opts.pull_up = 1;
    opts.threshold_us = 50;
    opts.debug = 0;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--chip") == 0 && i + 1 < argc) {
            chip_arg = argv[++i];
        } else if (strcmp(argv[i], "--gpio") == 0 && i + 1 < argc) {
            opts.gpio = (unsigned int)strtoul(argv[++i], NULL, 0);
        } else if (strcmp(argv[i], "--pull-up") == 0) {
            opts.pull_up = 1;
        } else if (strcmp(argv[i], "--no-pull-up") == 0) {
            opts.pull_up = 0;
        } else if (strcmp(argv[i], "--threshold-us") == 0 && i + 1 < argc) {
            opts.threshold_us = atoi(argv[++i]);
        } else if (strcmp(argv[i], "--debug") == 0) {
            opts.debug = 1;
        } else if (strcmp(argv[i], "--help") == 0) {
            usage(argv[0]);
            return 0;
        } else {
            usage(argv[0]);
            return 2;
        }
    }

    opts.chip = normalize_chip(chip_arg, chip_buffer, sizeof(chip_buffer));
    best_effort_realtime();

    struct reading reading;
    enum read_status status = read_sensor(&opts, &reading, errbuf, sizeof(errbuf));
    print_json(&opts, &reading);
    return status == READ_OK ? 0 : 1;
}
