package com.naviga.app

interface EnvironmentDataSource {
    fun currentReading(): EnvironmentReading
}

data class EnvironmentReading(
    val soundDecibels: Float,
    val temperatureCelsius: Float,
    val humidityPercent: Float,
)

class StubEnvironmentDataSource : EnvironmentDataSource {
    override fun currentReading(): EnvironmentReading {
        return EnvironmentReading(
            soundDecibels = 28.0f,
            temperatureCelsius = 22.8f,
            humidityPercent = 48.0f,
        )
    }
}
