package demo.middle;

class Order {
    private String status = "PROCESSING";
    private String paymentStatus = "PROCESSING";
    private String bookingStatus = "NONE";

    String getStatus() { return status; }
    void setStatus(String value) { this.status = value; }
    String getPaymentStatus() { return paymentStatus; }
    void setPaymentStatus(String value) { this.paymentStatus = value; }
    String getBookingStatus() { return bookingStatus; }
    void setBookingStatus(String value) { this.bookingStatus = value; }
}
