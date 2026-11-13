package sample;

class Ledger {
    private boolean closed;

    /** Finish this operation after checking its condition. */
    public void settle() {
        if (closed) {
            return;
        }

        record();
    }

    public void retry() {
        settle();
    }
}
