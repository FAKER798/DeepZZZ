package com.naviga.app

enum class FamilyRole(val label: String) {
    Mother("妈妈"),
    Father("爸爸"),
}

data class FamilyMember(
    val name: String,
    val role: FamilyRole,
    val online: Boolean,
)

fun familyMembers() = listOf(
    FamilyMember("妈妈", FamilyRole.Mother, online = true),
    FamilyMember("爸爸", FamilyRole.Father, online = true),
)
